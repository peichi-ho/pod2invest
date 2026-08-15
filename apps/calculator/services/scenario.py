# apps/calculator/services/scenario.py
"""
情境資產成長模擬（試算計算機核心邏輯）。

已驗證內容：
  - spread 公式用 annual_vol（波動率）驅動寬度，不是報酬率。
  - annual_vol 對 spread 的貢獻設上限 VOL_CAP，避免極端投機股數字失控。
  - k/R_MAX/L_MAX/ADD 已用182筆真實資料、70/30訓練測試切分校準過（測試集覆蓋率65.5%，
    優於demo初步參數的50.9%）。
  - BULL_MULT（樂觀情境獨立加寬）已用2454筆真實資料、70/30訓練測試切分校準過，
    測試集覆蓋率從68.9%提升到78.4%，訓練/測試落差比原始版本更小。
  - 因為資料庫目前只涵蓋15個月AI供應鏈特殊多頭期，這組參數是「目前資料限制下的最佳解」，
    不是最終定案，尤其BULL_MULT的不對稱比例還沒被空頭/盤整期資料驗證過。
  - GBM 模擬：基準情境獨立跑 N_PATHS 次算 10~90 百分位區間帶（誠實呈現不確定性）；
    樂觀/悲觀維持單一「示範路徑」，跟基準示範路徑共用同一組隨機亂數（避免不合理交錯，
    且線條保留真實震盪感，不是平滑曲線）。極端波動率股票（如 annual_vol > 100%）下，
    示範路徑可能因單次隨機抽樣運氣脫離區間帶之外，屬已知限制。
  - GBM 模擬的震盪來源：優先借用該股票自己過去5年的真實歷史月報酬做bootstrap
    （見 _generate_shocks），保留真實漲跌紋理；歷史不足才退回常態隨機數。
    用2452筆真實資料回測過：區間帶覆蓋率42.0%→43.4%、平均寬度137.4%→135.5%，雙贏。
"""
import math
import random
from dataclasses import dataclass, field

# ── 已校準參數（Task #4，測試集覆蓋率65.5%，見專案說明文件）───────────────────
K = 1.2
R_MAX = 1.0
L_MAX = 0.4
ADD = 0.4
VOL_CAP = 0.60      # annual_vol 對 spread 的貢獻上限
BEAR_FLOOR = -0.90  # 悲觀情境安全下限

# ── 樂觀情境獨立加寬（用2454筆真實資料、70/30訓練測試切分校準過）───────────────
# 原本樂觀/保守共用同一個spread，只靠lean決定往哪邊偏，但回測發現「實際報酬衝出樂觀情境
# 之外」佔了miss的大多數（~28%），「跌破保守情境」只佔一小部分（~5.5%）——不對稱。
# BULL_MULT只加寬樂觀這一邊，保守情境公式完全不變，測試集覆蓋率從68.9%提升到78.4%，
# 訓練/測試落差(-1.0pp)比原始版本(-2.8pp)還小，不是過擬合。
# 已知限制：這個不對稱比例是從15個月AI供應鏈多頭期量出來的，空頭/盤整期是否依然
# 成立尚未驗證，之後資料庫涵蓋更多市況後需要重新檢視。
BULL_MULT = 1.3

# ── 大盤指數獨立信號（草稿值，尚未用真實資料校準過，預設不啟用）──────────────────
# 跟 macro_score 不同：macro_score 是AI對這集節目主觀語氣的判斷，
# index_score 是大盤指數本身客觀的動能，兩者獨立加總進lean，不是拿指數去打折AI判斷
# （這個「打折」的版本已經拿182→2454筆真實資料回測過，會讓覆蓋率變差，見專案討論記錄）。
M_MAX = 0.2            # 大盤指數動能對lean的獨立貢獻上限
INDEX_NORMALIZE = 0.20  # 大盤指數近3個月漲跌±20%視為滿分±1
LEAN_CLAMP = 0.95       # 保護用，避免 (1±lean) 因為疊加後過大而變號

N_PATHS = 1000       # 基準情境統計區間帶的模擬次數
BAND_LOW_PCT = 10
BAND_HIGH_PCT = 90
MONTHS = 12


@dataclass
class ScenarioReturns:
    bull: float
    base: float
    bear: float


def compute_index_score(index_momentum: float) -> float:
    """把大盤指數近3個月的漲跌幅，正規化到 -1~1（用±20%當滿分，草稿值）。"""
    return max(-1.0, min(index_momentum / INDEX_NORMALIZE, 1.0))


def compute_scenario_returns(base: float, annual_vol: float, risk_score: float, macro_score: float,
                              index_score: float = 0.0) -> ScenarioReturns:
    """
    核心公式：算出樂觀/基準/悲觀三個情境的年化報酬率。

    index_score：大盤指數動能（已正規化到-1~1），預設0（=不啟用，跟原本公式完全一樣）。
    不是None，是刻意讓「沒有指數資料」跟「指數動能剛好是中性」共用同一個安全預設值。
    """
    vol_for_spread = min(annual_vol, VOL_CAP)
    spread = K * (1 + R_MAX * risk_score) * vol_for_spread + ADD * (0.3 + risk_score)
    lean = L_MAX * macro_score + M_MAX * index_score
    lean = max(-LEAN_CLAMP, min(lean, LEAN_CLAMP))

    bull = base + spread * (1 + lean) * BULL_MULT
    bear = max(base - spread * (1 - lean), BEAR_FLOOR)
    return ScenarioReturns(bull=bull, base=base, bear=bear)


def _generate_shocks(rng: random.Random, months: int, return_pool: list[float] = None) -> list[float]:
    """
    產生 months 個震盪值，餵給 _simulate_path 用。

    有提供 return_pool（該股票發布日之前的標準化歷史月報酬，見 views.py 的
    _fetch_historical_return_pool）就從裡面借一段連續 months 個月的真實區間（bootstrap），
    保留這支股票真實發生過的動能/回檔紋理，不是憑空生成的白噪音；
    沒有提供（例如歷史資料不足）就退回原本的常態分布隨機數，不影響現有行為。
    用2452筆真實資料回測過：bootstrap比常態隨機數的區間帶覆蓋率略高（43.4% vs 42.0%），
    平均寬度還略窄（135.5% vs 137.4%），是雙贏而不是取捨。
    """
    if return_pool and len(return_pool) >= months:
        start_idx = rng.randint(0, len(return_pool) - months)
        return return_pool[start_idx:start_idx + months]
    return [rng.gauss(0, 1) for _ in range(months)]


def _simulate_path(annual_return: float, annual_vol: float, start_price: float,
                    months: int, shocks: list[float]) -> list[float]:
    """用給定的隨機震盪序列，跑一條 GBM 路徑（月為單位）。"""
    monthly_drift = math.log(1 + annual_return) / 12
    monthly_vol = annual_vol / math.sqrt(12)
    price = start_price
    row = [price]
    for z in shocks:
        price = price * math.exp(monthly_drift + monthly_vol * z - 0.5 * monthly_vol ** 2)
        row.append(price)
    return row


def _percentile(values: list[float], pct: float) -> float:
    values = sorted(values)
    idx = (len(values) - 1) * pct / 100
    lo, hi = int(math.floor(idx)), int(math.ceil(idx))
    if lo == hi:
        return values[lo]
    frac = idx - lo
    return values[lo] * (1 - frac) + values[hi] * frac


HALF_LIFE_DAYS = 90  # 時間加權半衰期，已用真實資料驗證過（見Step D），對結果不算敏感


def compute_time_weighted_scores(ticker: str, selected_date, latest_date=None, half_life_days: float = HALF_LIFE_DAYS):
    """
    「最新綜合預測」模式：從 selected_date 這一集到 latest_date(預設=資料庫裡這支股票最新一集)
    之間，所有討論過這支股票的集數，依照『距離 latest_date 多久』做半衰期加權平均。

    只回傳 macro_score / risk_score 的加權結果；base/annual_vol/start_price 不在這裡處理，
    維持使用者選定那一集自己的真實數字（呼叫端負責帶入，不要被這個函式的結果覆蓋）。
    """
    from apps.summaries.models import StockSentimentScore

    # 只用 pro 版本：novice 是從 pro 改寫成白話文字再重新分類，同一集會產生兩筆
    # StockSentimentScore，全部納入等於同一集被加權兩次。pro/novice 是嚴格一對一
    # 配對（見 novice_content.py），只篩 pro 不會漏掉任何一集。
    rows = list(
        StockSentimentScore.objects.using("summariesdb")
        .filter(ticker=ticker, summary__mode="pro")
        .select_related("summary")
        .order_by("summary__published_at")
    )
    eligible = [r for r in rows if r.summary.published_at and r.summary.published_at.date() >= selected_date]
    if not eligible:
        return None

    if latest_date is None:
        latest_date = max(r.summary.published_at.date() for r in eligible)

    weighted_items = []
    total_w = 0.0
    weighted_macro = 0.0
    weighted_risk = 0.0
    for r in eligible:
        pub_date = r.summary.published_at.date()
        days_ago = (latest_date - pub_date).days
        w = 0.5 ** (days_ago / half_life_days)
        total_w += w
        weighted_macro += w * r.macro_score
        weighted_risk += w * r.risk_score
        weighted_items.append({
            "summary_id": r.summary_id,
            "asset_name": r.asset_name,
            "podcaster": r.summary.podcaster,
            "published_at": pub_date.isoformat(),
            "weight": w,
            "macro_score": r.macro_score,
            "risk_score": r.risk_score,
        })

    weighted_items.sort(key=lambda x: -x["weight"])

    return {
        "macro_score": weighted_macro / total_w,
        "risk_score": weighted_risk / total_w,
        "n_episodes": len(eligible),
        "latest_date": latest_date.isoformat(),
        "top_contributors": weighted_items[:3],  # 給前端「主要影響節目」用
    }


@dataclass
class ScenarioChartData:
    months: list[int]
    bull_line: list[float]
    base_line: list[float]      # 基準「示範路徑」，跟樂觀/悲觀共用隨機亂數
    bear_line: list[float]
    base_band_low: list[float]  # 基準統計區間帶（獨立跑N_PATHS次，跟上面示範線是兩回事）
    base_band_high: list[float]
    returns: ScenarioReturns = field(default_factory=lambda: ScenarioReturns(0, 0, 0))


def build_scenario_chart(base: float, annual_vol: float, risk_score: float, macro_score: float,
                          start_price: float, seed: int = None, months: int = MONTHS,
                          index_score: float = 0.0, return_pool: list[float] = None) -> ScenarioChartData:
    """
    給定輸入，回傳畫圖用的完整資料：
    三條有真實震盪的示範線 + 基準的統計區間帶。

    months：模擬要跑幾個月，預設12（=MONTHS），由呼叫端依使用者選的投資期間決定，
    讓圖表跟左側「投資期間」對齊，不是永遠固定跑12個月。
    index_score：大盤指數獨立信號，預設0（=不啟用，跟原本公式完全一樣）。
    return_pool：該股票發布日之前的標準化歷史月報酬（見 _generate_shocks），
    沒有提供就退回常態隨機數，跟原本行為完全一樣。
    """
    returns = compute_scenario_returns(base, annual_vol, risk_score, macro_score, index_score)

    rng = random.Random(seed)
    shared_shocks = _generate_shocks(rng, months, return_pool)

    bull_line = _simulate_path(returns.bull, annual_vol, start_price, months, shared_shocks)
    base_line = _simulate_path(returns.base, annual_vol, start_price, months, shared_shocks)
    bear_line = _simulate_path(returns.bear, annual_vol, start_price, months, shared_shocks)

    band_rng = random.Random((seed or 0) + 1000)
    band_paths = []
    for _ in range(N_PATHS):
        shocks = _generate_shocks(band_rng, months, return_pool)
        band_paths.append(_simulate_path(returns.base, annual_vol, start_price, months, shocks))

    band_low, band_high = [], []
    for m in range(months + 1):
        col = [p[m] for p in band_paths]
        band_low.append(_percentile(col, BAND_LOW_PCT))
        band_high.append(_percentile(col, BAND_HIGH_PCT))

    return ScenarioChartData(
        months=list(range(months + 1)),
        bull_line=bull_line,
        base_line=base_line,
        bear_line=bear_line,
        base_band_low=band_low,
        base_band_high=band_high,
        returns=returns,
    )
