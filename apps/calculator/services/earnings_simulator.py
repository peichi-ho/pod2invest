# apps/calculator/services/earnings_simulator.py
"""
Podcast Thesis Simulator 的財報試算引擎（對應規劃裡的第2、3點）。

跟 scenario.py 的 GBM/spread-lean 統計模型是平行的兩套機制，互不依賴：
scenario.py 直接統計預測報酬率的分布；這裡反過來，從財務假設(營收成長率、
毛利率、營業利益率、P/E)一路算到目標價，報酬率是算出來的結果，不是使用者
直接輸入的猜測值。
"""
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from typing import Optional


@dataclass
class FairValueResult:
    revenue: float
    gross_profit: float
    ebit: float
    net_income: float
    eps: float
    fair_value: float
    implied_return: Optional[float] = None


def compute_fair_value(
    base_revenue: float,
    growth_rate: float,
    gross_margin: float,
    operating_margin: float,
    tax_rate: float,
    shares_outstanding: float,
    pe: float,
    current_price: Optional[float] = None,
) -> FairValueResult:
    """
    財報試算鏈：營收 → 毛利/營業利益 → 淨利 → EPS → 目標價 → (可選)隱含報酬率。

    純函式，不碰資料庫、不呼叫外部API，所有輸入都由呼叫端決定——對應「Earnings
    Simulator」跟「報酬率改成輸出」這兩個功能。Monte Carlo(第4點)會用不同的
    抽樣輸入重複呼叫這個函式，所以這裡刻意不做輸入範圍檢查(例如不擋負毛利率)，
    虧損季度的負利潤率是真實會發生的情況，不是異常值。

    operating_margin 直接乘營收算 ebit，不是從 gross_profit 再扣營業費用——
    這樣使用者只要調兩個獨立比率就好，不用額外輸入營業費用金額；gross_profit
    只當參考資訊回傳，不會再往下影響 ebit/net_income。
    """
    if base_revenue <= 0:
        raise ValueError("base_revenue 必須是正數")
    if shares_outstanding <= 0:
        raise ValueError("shares_outstanding 必須是正數")
    if pe <= 0:
        raise ValueError("pe 必須是正數")

    revenue = base_revenue * (1 + growth_rate)
    gross_profit = revenue * gross_margin
    ebit = revenue * operating_margin
    net_income = ebit * (1 - tax_rate)
    eps = net_income / shares_outstanding
    # EPS為負時，「EPS×P/E」算出來的合理股價會是負的，但股價不可能是負的
    # (股東最多虧到歸零，不會變成負資產)——用P/E做估值本來就只在獲利為正時
    # 有意義，EPS<0時沒有真正合理的P/E估值方法，這裡保守地把合理股價底線設
    # 在0，不讓報酬率算出低於-100%這種現實中不可能發生的結果。
    fair_value = max(eps * pe, 0.0)

    implied_return = None
    if current_price is not None and current_price > 0:
        implied_return = fair_value / current_price - 1

    return FairValueResult(
        revenue=revenue,
        gross_profit=gross_profit,
        ebit=ebit,
        net_income=net_income,
        eps=eps,
        fair_value=fair_value,
        implied_return=implied_return,
    )


# 同比比對允許的誤差範圍——財報公告日期每期會有一些漂移(例如去年Q2是5/14公告，
# 今年Q2可能是5/9)，抓過去365天前「最接近」的那一期，而不是硬性要求剛好365天。
YOY_TOLERANCE_DAYS = 45


def get_historical_growth_rates(
    ticker: str, metric: str = "revenue", min_periods: int = 8,
    as_of_date: Optional[date] = None,
) -> list[float]:
    """
    抓這檔股票歷史上「同比(YoY)」的成長率序列，當 Monte Carlo 抽樣的母體。

    metric 對應 FinancialReportCache 的數值欄位名稱("revenue"/"net_income"/
    "ebit" 等)。每一筆成長率是拿某一期跟「約365天前最接近的那一期」比較算出來
    的，不是跟資料庫裡緊鄰的前一筆比較——季度資料如果直接抓相鄰兩期，比的其實
    是環比（例如Q4對比Q3），會把季節性波動誤判成真正的成長趨勢。

    as_of_date 給定時，只用 disclosure_date <= as_of_date 的期別(這一期財報
    在 as_of_date 當下已經真的公告過了)，給 backtest 用——避免拿「未來才會
    公告」的財報去模擬「過去」某個時間點，會不小心偷看到當時不可能知道的
    資訊(look-ahead bias)。留 None(預設)給正式功能用，維持原本「用全部
    現有資料」的行為不變。

    回傳的 list 筆數 < min_periods 時，代表這檔股票自己的樣本不夠，呼叫端
    (Monte Carlo/get_industry_pe_samples 的姊妹函式)要自己決定要不要 fallback
    到同產業其他公司的資料，這裡不做任何 fallback，只誠實回報「有多少就是多少」。
    """
    from apps.summaries.models import FinancialReportCache

    qs = FinancialReportCache.objects.using("summariesdb").filter(
        ticker=ticker, **{f"{metric}__isnull": False}
    )
    if as_of_date is not None:
        qs = qs.filter(disclosure_date__lte=as_of_date)
    rows = list(qs.order_by("fiscal_period_end").values("fiscal_period_end", metric))
    if len(rows) < 2:
        return []

    growth_rates = []
    for i, row in enumerate(rows):
        target_date = row["fiscal_period_end"] - timedelta(days=365)
        # 從這一筆之前的資料裡，找離 target_date 最近、且在容許誤差內的那一期
        best_match = None
        best_diff = None
        for prior in rows[:i]:
            diff = abs((prior["fiscal_period_end"] - target_date).days)
            if diff <= YOY_TOLERANCE_DAYS and (best_diff is None or diff < best_diff):
                best_match, best_diff = prior, diff

        if best_match is None:
            continue
        base_value = best_match[metric]
        if base_value is None or base_value == 0:
            continue
        growth_rates.append(row[metric] / base_value - 1)

    return growth_rates


def get_historical_margins(
    ticker: str, margin_type: str = "gross", as_of_date: Optional[date] = None,
) -> list[float]:
    """
    抓這檔股票歷史上的毛利率/營業利益率序列。

    margin_type: "gross"(毛利率 = gross_profit / revenue) 或
    "operating"(營業利益率 = ebit / revenue)。

    as_of_date 語意跟 get_historical_growth_rates 一致：給定時只用
    disclosure_date <= as_of_date 的期別，給 backtest 用；None 用全部現有資料。

    不需要像 get_historical_growth_rates 那樣做同比比對——毛利率是「這一期
    自己的比率」，不是跟去年比的變化量，資料庫裡每一期只要revenue跟對應的
    分子欄位都不是null，就可以直接拿來算一筆。
    """
    from apps.summaries.models import FinancialReportCache

    if margin_type not in ("gross", "operating"):
        raise ValueError('margin_type 必須是 "gross" 或 "operating"')
    numerator_field = "gross_profit" if margin_type == "gross" else "ebit"

    qs = FinancialReportCache.objects.using("summariesdb").filter(
        ticker=ticker, revenue__isnull=False, **{f"{numerator_field}__isnull": False}
    )
    if as_of_date is not None:
        qs = qs.filter(disclosure_date__lte=as_of_date)
    rows = qs.values("revenue", numerator_field)

    margins = []
    for row in rows:
        if row["revenue"] == 0:
            continue
        margins.append(row[numerator_field] / row["revenue"])
    return margins


TWSE_INDUSTRY_LIST_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"


@lru_cache(maxsize=1)
def _fetch_twse_industry_map() -> dict[str, str]:
    """
    抓台灣證交所官方產業別分類，回傳 {股票代號(不含.TW後綴): 產業別代碼}。

    lru_cache(maxsize=1) 代表這個function只在process生命週期內第一次呼叫
    時真的打API，之後都直接回傳快取的結果——公司的產業別歸屬幾乎不會在
    短時間內改變，不需要每次呼叫都重新打一次TWSE的API。如果之後要在
    Django app裡長期運作、需要跨process共享或定期更新，建議改成用
    Django cache framework或存進資料庫表，這裡先用最簡單的做法。
    """
    import urllib.request
    import json as json_module

    req = urllib.request.Request(TWSE_INDUSTRY_LIST_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json_module.loads(resp.read())
    return {row["公司代號"]: row["產業別"] for row in data}


def _get_twse_industry_peers(industry_code: str) -> list[str]:
    """回傳台灣證交所官方分類下，屬於同一個產業別代碼的全部股票代號(不含.TW後綴)。"""
    industry_map = _fetch_twse_industry_map()
    return [num for num, code in industry_map.items() if code == industry_code]


def _get_industry_peer_tickers(ticker: str, max_peers: int = 30) -> list[str]:
    """
    回傳同產業其他公司的完整ticker(含.TW後綴)清單；非台股回傳空list。

    抽出來當共用helper，因為「找同產業peer」這件事，get_industry_pe_samples
    跟 run_earnings_monte_carlo 的樣本不足fallback都需要用到，不要各寫一份。
    """
    is_tw = ticker.endswith(".TW") or ticker.endswith(".TWO")
    if not is_tw:
        return []

    num = ticker.rsplit(".", 1)[0]
    industry_map = _fetch_twse_industry_map()
    industry_code = industry_map.get(num)
    if industry_code is None:
        return []

    peer_nums = [n for n in _get_twse_industry_peers(industry_code) if n != num][:max_peers]
    return [f"{n}.TW" for n in peer_nums]


def _filter_outliers_iqr(values: list[float]) -> list[float]:
    """
    用四分位距(IQR)法篩掉離群值——Tukey's fences，合理範圍是
    [Q1-1.5*IQR, Q3+1.5*IQR]，是統計學處理離群值的通用慣例，不是自訂門檻。

    樣本數少於4筆時，四分位數本身估得不穩，直接跳過篩選、全部保留，避免
    在小樣本下用不可靠的Q1/Q3反而篩掉正常值。
    """
    if len(values) < 4:
        return values
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    q1 = sorted_vals[n // 4]
    q3 = sorted_vals[(3 * n) // 4]
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [v for v in values if lo <= v <= hi]


def get_industry_pe_samples(ticker: str, max_peers: int = 30) -> list[float]:
    """
    抓這檔股票所屬產業，同產業其他公司的本益比，組成一批可以拿去 Monte Carlo
    抽樣的母體。

    用 forward P/E(分析師對未來12個月獲利共識預估算出的本益比)，不用
    trailing P/E——因為 compute_fair_value 算出來的EPS是套用成長率之後的
    「未來」EPS，本益比也要用同樣「未來」的時間基準才對得起來，混用
    trailing P/E 會讓過去跟未來的時間基準對不齊。代價是規模較小、分析師
    覆蓋較少的同業，可能查不到 forward P/E，樣本數會比用 trailing P/E 少。

    抓回來的原始P/E會先用 _filter_outliers_iqr 篩掉明顯異常的離群值(例如
    獲利趨近於零、本益比被放大到幾百倍的個案)，再回傳——真實分析師做可比
    公司分析時，也會先排除這種不具代表性的同業，不會照單全收。

    台股(.TW/.TWO結尾)用台灣證交所官方產業別分類抓同業清單；非台股股票，
    目前沒有一個像TWSE那樣涵蓋全球市場的官方分類可以直接用，這個函式對
    非台股直接回傳空list——呼叫端要自己決定 fallback 怎麼處理(例如退
    回這檔股票自己的歷史P/E，或維持使用者手動輸入的固定P/E)。

    max_peers 限制最多抓幾檔同業的P/E，避免產業成分股過多時(例如電子
    零組件業一次抓100家)拖慢太多時間——同業樣本超過這個數字時，只抽前
    max_peers檔，不是全部都抓。
    """
    import yfinance as yf

    peer_tickers = _get_industry_peer_tickers(ticker, max_peers=max_peers)

    pe_samples = []
    for peer_ticker in peer_tickers:
        try:
            info = yf.Ticker(peer_ticker).info
            pe = info.get("forwardPE")
            if pe is not None and pe > 0:
                pe_samples.append(pe)
        except Exception:
            continue

    return _filter_outliers_iqr(pe_samples)


def _pool_with_industry_fallback(
    ticker, fetch_fn, min_periods: int, max_peers: int = 10,
    as_of_date: Optional[date] = None,
) -> list[float]:
    """
    先抓這檔股票自己的樣本；不夠 min_periods 筆，就逐一併入同產業peer的樣本，
    抓到夠了就停手，不會把整個產業全部peer的資料都抓一遍。

    fetch_fn 是一個「輸入ticker、回傳list[float]」的函式——呼叫端會傳
    get_historical_growth_rates 或 get_historical_margins 的某個固定參數版本
    進來(用 functools.partial 綁定 metric/margin_type)。

    as_of_date 有給時，連 fallback 抓 peer 的樣本也一併套用同一個時間點限制
    (peer 的資料也只能用當時已公告的)，不是只擋自己這檔股票的資料。
    """
    pool = list(fetch_fn(ticker, as_of_date=as_of_date))
    if len(pool) >= min_periods:
        return pool

    for peer_ticker in _get_industry_peer_tickers(ticker, max_peers=max_peers):
        pool.extend(fetch_fn(peer_ticker, as_of_date=as_of_date))
        if len(pool) >= min_periods:
            break
    return pool


def get_consensus_growth_estimates(ticker: str) -> list[float]:
    """
    抓分析師對「未來一年」EPS的共識預估(平均/最低/最高)，換算成3個成長率
    樣本點，回傳 [悲觀共識成長率, 平均共識成長率, 樂觀共識成長率]。

    純看歷史成長率去外推未來，不是真實分析師預估未來的主要依據——分析師
    通常是基於公司財測、產業展望等前瞻資訊給出預估，跟純粹外推過去數字
    是兩種不同的資訊來源。這裡把這批共識預估當成歷史成長率之外的「額外」
    樣本點，兩者混在同一個抽樣池裡，讓 Monte Carlo 同時參考「過去真的發
    生過什麼」跟「專業分析師現在怎麼看未來」，不是用其中一個取代另一個。

    抓不到分析師預估資料(通常是規模較小、沒什麼分析師覆蓋的公司)時回傳
    空list，呼叫端會自動只用歷史成長率池，不會因為這裡沒有資料就整個失敗。
    """
    import yfinance as yf

    try:
        ee = yf.Ticker(ticker).earnings_estimate
        row = ee.loc["+1y"]
        base = row["yearAgoEps"]
        if base is None or base == 0:
            return []
        return [
            row["low"] / base - 1,
            row["avg"] / base - 1,
            row["high"] / base - 1,
        ]
    except Exception:
        return []


def _apply_podcast_adjustment(returns: list[float], macro_score: float, risk_score: float) -> list[float]:
    """
    對模擬結果做小幅調整，反映Podcast的方向/不確定性判斷——只做「平移+圍繞
    原本中心小幅縮放」，不重新決定抽樣範圍本身。

    調整幅度刻意設得保守(平移最大±2pp、寬度最多放大10%)，因為已經驗證過
    risk_score/macro_score對統計預測的貢獻度本來就有限，這裡只當輔助訊號，
    範圍主體還是交給真實財報/P/E歷史資料決定，不是Podcast判斷說了算。
    """
    if macro_score == 0 and risk_score == 0:
        return returns

    mean_r = sum(returns) / len(returns)
    shift = macro_score * 0.02
    widen = 1 + risk_score * 0.1
    return [mean_r + (r - mean_r) * widen + shift for r in returns]


@dataclass
class MonteCarloResult:
    n_simulations: int
    returns: list[float]
    median_return: float
    p10: float
    p90: float
    prob_of_profit: float
    prob_above_20pct: float
    downside_5pct: float


def run_earnings_monte_carlo(
    ticker: str,
    base_revenue: float,
    shares_outstanding: float,
    current_price: float,
    tax_rate: float = 0.20,
    n_simulations: int = 10000,
    macro_score: float = 0.0,
    risk_score: float = 0.0,
    min_pool_periods: int = 8,
    seed: Optional[int] = None,
    as_of_date: Optional[date] = None,
) -> MonteCarloResult:
    """
    對應規劃裡的第4點：不直接模擬股價路徑，改成拔靴抽樣「營收成長率、毛利率、
    營業利益率、P/E」這幾個真實歷史數字，每次抽一組、套 compute_fair_value
    算一次結果，重複 n_simulations 次，最後統計整批結果。

    base_revenue 務必給「近四季合計(TTM)營收」，不要給單一季營收——因為
    get_industry_pe_samples 抓的是 trailingPE(用TTM EPS算的本益比)，
    base_revenue 如果只給單季、算出來的是單季EPS，拿去乘TTM口徑的P/E，
    兩邊基準對不上，算出來的fair_value會嚴重失真(這是實際測試台積電時
    真的踩到的問題，不是理論上的提醒)。

    四個抽樣母體，任何一個抓不到資料(例如這檔股票不是台股、抓不到P/E同業
    樣本)就會直接丟例外，不會默默地拿一個瞎猜的範圍去頂替——寧可讓呼叫端
    知道「這檔股票現在做不了這個模擬」，也不要生出一個看似正常、實際上沒
    有真實資料支撐的結果。

    as_of_date 給定時，用於 backtest：growth_pool/gross_margin_pool/
    operating_margin_pool 這三個從 FinancialReportCache 抓的母體，只會用
    disclosure_date <= as_of_date 的期別。但 pe_pool(get_industry_pe_samples)
    跟 consensus growth(get_consensus_growth_estimates)這兩個是直接打
    yfinance 抓「現在」的 forwardPE/分析師預估，這裡沒有歷史P/E或歷史
    分析師共識的資料庫可以查——這兩個母體無法真正做到「as_of_date當時」，
    一定是用「現在」的值，這是目前架構下backtest不完全嚴謹的已知限制，
    不是遺漏忘了接，之後如果要補歷史P/E/共識資料，才可能徹底解決。
    """
    import random
    from functools import partial

    growth_pool = _pool_with_industry_fallback(
        ticker, partial(get_historical_growth_rates, metric="revenue"), min_pool_periods,
        as_of_date=as_of_date,
    )
    # 併入分析師對未來的共識成長率預估，不是取代歷史池，是額外補充——
    # 讓抽樣同時涵蓋「過去真實發生過的」跟「專業分析師現在怎麼看未來」。
    # 注意：這裡抓的是「現在」的共識預估，as_of_date 對這個母體不生效(見上方docstring)。
    growth_pool = growth_pool + get_consensus_growth_estimates(ticker)
    gross_margin_pool = _pool_with_industry_fallback(
        ticker, partial(get_historical_margins, margin_type="gross"), min_pool_periods,
        as_of_date=as_of_date,
    )
    operating_margin_pool = _pool_with_industry_fallback(
        ticker, partial(get_historical_margins, margin_type="operating"), min_pool_periods,
        as_of_date=as_of_date,
    )
    # 注意：這裡抓的是「現在」的同業forward P/E，as_of_date 對這個母體不生效(見上方docstring)。
    pe_pool = get_industry_pe_samples(ticker)

    missing = [
        name for name, pool in [
            ("growth", growth_pool), ("gross_margin", gross_margin_pool),
            ("operating_margin", operating_margin_pool), ("pe", pe_pool),
        ] if not pool
    ]
    if missing:
        raise ValueError(f"{ticker} 抽樣母體不足，缺少: {', '.join(missing)}，無法執行模擬")

    rng = random.Random(seed)
    returns = []
    for _ in range(n_simulations):
        result = compute_fair_value(
            base_revenue=base_revenue,
            growth_rate=rng.choice(growth_pool),
            gross_margin=rng.choice(gross_margin_pool),
            operating_margin=rng.choice(operating_margin_pool),
            tax_rate=tax_rate,
            shares_outstanding=shares_outstanding,
            pe=rng.choice(pe_pool),
            current_price=current_price,
        )
        returns.append(result.implied_return)

    returns = _apply_podcast_adjustment(returns, macro_score, risk_score)

    returns_sorted = sorted(returns)
    n = len(returns_sorted)
    return MonteCarloResult(
        n_simulations=n_simulations,
        returns=returns,
        median_return=returns_sorted[n // 2],
        p10=returns_sorted[int(n * 0.10)],
        p90=returns_sorted[int(n * 0.90)],
        prob_of_profit=sum(1 for r in returns if r > 0) / n,
        prob_above_20pct=sum(1 for r in returns if r > 0.20) / n,
        downside_5pct=returns_sorted[int(n * 0.05)],
    )
