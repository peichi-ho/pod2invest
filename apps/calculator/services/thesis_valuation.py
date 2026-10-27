# apps/calculator/services/thesis_valuation.py
"""
論點情境試算的第二步：每家公司的「基準數字」，以及把勾選的情境推算成 EPS 與目標價。

流程(第一步見 apps/summaries/services/thesis_scenario.py)：
    驗證句 → 情境(需求↑、漲價、毛利改善…) → 財務參數(營收成長、毛利率、費用率、本益比)
           → 本檔：近四季基準 × 調整 → 未來12個月 EPS → × 本益比 → 目標價

三個原則：
  1. 基準一律用「當時已公告」的近四季財報(disclosure_date <= as_of)，回測舊節目時不偷看未來。
  2. 以「現在」為基準調整(2026-10-26 起)：都不勾時，營收照目前的年增率、利潤率與本益比維持現值；
     勾一個情境就是「比現在好一點／差一點」。歷史只決定「一次調多少」——這家公司過去 5 年
     這個數字通常上下波動多少(P75−P50 或 P50−P25)，不再決定「調到哪個百分位」。
     改的原因：原本營收成長是直接跳到歷史 P75/P25，但回測樣本有 47% 的公司當時成長率已經高於 P75，
     勾了利多反而比現在還低；而且拿「目前年增率」預估隔年營收成長，誤差中位數 9.2 個百分點，
     比用歷史中位數的 13.4 個百分點小(scripts/backtest_thesis_scenarios.py 同一批樣本)。
     少數還沒有歷史可依的(財務風險、總經、法規)用暫定值，並在結果裡標 provisional=True。
  3. 依資料完整度自動選計算路線(route)，資料不夠就說明原因，不硬算：
       full     營收 → 毛利率 − 費用率 → 營業利益 → EPS → × 本益比
       no_gross 缺毛利(金融以外約13檔美股)：營收 → 營業利益率 → EPS → × 本益比
       net      連營業利益都沒有申報(例如 GE)：營收 → 淨利率 → EPS → × 本益比
       ps       近四季虧損：本益比沒有意義，改用股價營收比(P/S) × 預估營收
       不支援    指數/商品/ETF、銀行壽險、還沒有營收、財報不足四季

EPS 怎麼從營業利益推回來：EPS' = 近四季EPS × (預估營業利益 ÷ 近四季營業利益)。等於假設稅率、
業外損益、股數都跟營業利益同比例變動——比另外假設一個稅率簡單，而且「什麼都不調」時會剛好
回到近四季 EPS。已知限制：近四季業外損益特別大的公司(例如一次性處分利益)，推算會跟著放大。

舊的 apps/calculator/services/earnings_simulator.compute_fair_value 是直接用營業利益率乘營收，
毛利率不會往下影響 EPS——勾選「毛利改善」完全沒有效果。這裡改成 營業利益率 = 毛利率 − 費用率。
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np

HISTORY_YEARS = 5
MIN_HISTORY_POINTS = 4          # 歷史分布少於這麼多個點，就不給百分位(退回暫定值)
MIN_STEP = {"revenue_growth": 0.05, "gross_margin": 0.01, "opex_ratio": 0.005,
            "operating_margin": 0.01, "net_margin": 0.01}
# 利潤率一次最多調整現值的這個比例：GE 這種分拆過好幾次的公司，歷史淨利率波動極大，
# 用(P50−P25)當步幅會讓 Bear 情境的 EPS 直接掉八成，不是「一個情境」該有的幅度
MAX_MARGIN_STEP_RATIO = 0.25
MAX_GROWTH_STEP = 0.25          # 營收成長一次最多調 25 個百分點(輝達這類成長率波動極大的公司)
GROWTH_CLAMP = (-0.5, 1.0)      # 目前年增率當基準時的合理範圍，超出的視為基期效應
MULTIPLE_STEP = (0.05, 0.25)    # 本益比/P/S 一次調整幅度：最少現值的 5%、最多 25%
# 近四季淨利超過營業利益的這個倍數，視為含大額業外收益(一般公司淨利約是營業利益的 0.8~0.95 倍)。
# 例：Google 2026 上半年股權投資大幅增值，近四季 EPS 19.91，本業只有 11~12，本益比看起來只有 16 倍。
NONOP_RATIO = 1.2
# 營業利益率(或淨利率)低於這個值，「預估獲利 ÷ 近四季獲利」的比例會被極度放大
# (利潤率 1% 時調 1 個百分點，EPS 就翻倍)，改走其他路線
THIN_MARGIN = 0.03
PE_CLAMP = (3.0, 150.0)         # 本益比合理範圍，超出的歷史點視為資料異常(例如EPS趨近0)
PS_CLAMP = (0.1, 100.0)

# 還沒有歷史分布可以校準的情境：暫定幅度，結果會標 provisional，給老師確認後再調
PROVISIONAL = {
    "FINANCIAL_RISK": -0.05,    # EPS × (1 − 5%)
    "MACRO": 0.5,               # 降息/升息：本益比往歷史 P75/P25 移動一半的距離
}

# 情境類型 → 影響哪個參數、往哪個方向(跟 thesis_scenario.SCENARIO_TYPES 的 levers 一致)
LEVERS = {
    "DEMAND_UP":          {"revenue_growth": +1},
    "NEW_BUSINESS":       {"revenue_growth": +1},
    "M_AND_A":            {"revenue_growth": +1},
    "PRICE_UP":           {"revenue_growth": +1, "gross_margin": +1},
    "MIX_UPGRADE":        {"gross_margin": +1},
    "COST_EFFICIENCY":    {"gross_margin": +1, "opex_ratio": -1},
    "CAPACITY_EXPANSION": {"revenue_growth": +1, "gross_margin": -1},
    "DEMAND_DOWN":        {"revenue_growth": -1},
    "COMPETITION":        {"revenue_growth": -1, "gross_margin": -1},
    "COST_UP":            {"gross_margin": -1},
    "PRICE_DOWN":         {"revenue_growth": -1, "gross_margin": -1},
    "RERATING_UP":        {"pe": +1},
    "RERATING_DOWN":      {"pe": -1},
    "MACRO_EASING":       {"pe": +1},
    "MACRO_TIGHTENING":   {"pe": -1},
    "FINANCIAL_RISK":     {"eps_multiplier": -1},
    "REGULATORY_RISK":    {"pe": -1},
}


def scenario_levers(s: dict) -> dict:
    """
    一個情境實際會動哪些參數。大多數照 LEVERS 對照表；擴產只有在 margin_drag(原文提到海外廠、
    新廠、折舊、稀釋)時才讓毛利率下降——2026-10-26 抽樣發現聯電借 Intel 閒置產能、Meta 的
    自用資本支出都不會稀釋毛利。margin_drag 由 thesis_scenario.apply_checks 標記。
    """
    levers = dict(LEVERS.get(s.get("type"), {}))
    if s.get("type") == "CAPACITY_EXPANSION" and not s.get("margin_drag"):
        levers.pop("gross_margin", None)
    # 成本下降：良率、原料這類生產成本只影響毛利率；裁員、精簡這類只影響營業費用率(cost_kind 由
    # thesis_scenario.apply_checks 依原文標記，看不出來的兩個都動)
    if s.get("type") == "COST_EFFICIENCY":
        if s.get("cost_kind") == "cogs":
            levers.pop("opex_ratio", None)
        elif s.get("cost_kind") == "opex":
            levers.pop("gross_margin", None)
    return levers


# ── 近四季(TTM)與歷史序列 ────────────────────────────────────────────────

def _consecutive(rows: list) -> bool:
    """四季必須連續(相鄰約3個月，52/53週制的Q4可到約112天)，中間缺一季就不能加總。"""
    ends = [r["fiscal_period_end"] for r in rows]
    return all(70 <= (a - b).days <= 120 for a, b in zip(ends, ends[1:]))


def ttm_series(rows: list, as_of: date) -> list:
    """
    as_of 當時已公告的季度，每一個「連續四季」的視窗算一個近四季合計，由新到舊回傳：
    [{"end", "disclosed", "revenue", "gross_profit", "operating_income", "net_income", "eps"}, ...]
    某個欄位四季裡有任一季是空的，那個欄位就是 None(不拿三季去湊)。
    """
    avail = sorted(
        (r for r in rows if r.get("disclosure_date") and r["disclosure_date"] <= as_of),
        key=lambda r: r["fiscal_period_end"], reverse=True,
    )
    out = []
    for i in range(len(avail) - 3):
        window = avail[i:i + 4]
        if not _consecutive(window):
            continue
        item = {"end": window[0]["fiscal_period_end"], "disclosed": window[0]["disclosure_date"]}
        for f in ("revenue", "gross_profit", "operating_income", "net_income", "eps"):
            vals = [r.get(f) for r in window]
            item[f] = float(sum(vals)) if all(v is not None for v in vals) else None
        out.append(item)
    return out


def _percentiles(values: list) -> Optional[dict]:
    vals = [v for v in values if v is not None and np.isfinite(v)]
    if len(vals) < MIN_HISTORY_POINTS:
        return None
    p25, p50, p75 = np.percentile(vals, [25, 50, 75])
    return {"p25": float(p25), "p50": float(p50), "p75": float(p75), "n": len(vals)}


def history_distributions(series: list, prices: Optional[dict] = None, years: int = HISTORY_YEARS) -> dict:
    """
    過去 years 年的近四季序列 → 各參數的百分位：
      revenue_growth：近四季營收年增率(跟四季前的近四季比)
      gross_margin / operating_margin / opex_ratio(=毛利率−營業利益率)
      pe：公告日股價 ÷ 當時近四季 EPS(只取 EPS>0、落在 PE_CLAMP 內的點)
      ps：公告日市值 ÷ 近四季營收(股數用 淨利 ÷ EPS 反推)
    prices：{date: 收盤價}，沒給就不算 pe/ps。
    """
    if not series:
        return {}
    cutoff = series[0]["end"] - timedelta(days=int(years * 365.25))
    recent = [s for s in series if s["end"] >= cutoff]
    by_end = {s["end"]: s for s in series}

    growth, gm, om, opex, nm, pe, ps = [], [], [], [], [], [], []
    for s in recent:
        rev = s["revenue"]
        prior = next((p for e, p in by_end.items() if 350 <= (s["end"] - e).days <= 380), None)
        if rev and prior and prior["revenue"] and prior["revenue"] > 0:
            growth.append(rev / prior["revenue"] - 1)
        if rev and rev > 0:
            if s["gross_profit"] is not None:
                gm.append(s["gross_profit"] / rev)
            if s["operating_income"] is not None:
                om.append(s["operating_income"] / rev)
            if s["gross_profit"] is not None and s["operating_income"] is not None:
                opex.append((s["gross_profit"] - s["operating_income"]) / rev)
            if s["net_income"] is not None:
                nm.append(s["net_income"] / rev)
        price = _price_on(prices, s["disclosed"]) if prices else None
        if price and s["eps"] and s["eps"] > 0:
            v = price / s["eps"]
            if PE_CLAMP[0] <= v <= PE_CLAMP[1]:
                pe.append(v)
        if price and rev and rev > 0 and s["eps"] and s["net_income"]:
            shares = s["net_income"] / s["eps"]
            if shares > 0:
                v = price * shares / rev
                if PS_CLAMP[0] <= v <= PS_CLAMP[1]:
                    ps.append(v)

    return {k: d for k, d in {
        "revenue_growth": _percentiles(growth), "gross_margin": _percentiles(gm),
        "operating_margin": _percentiles(om), "opex_ratio": _percentiles(opex), "net_margin": _percentiles(nm),
        "pe": _percentiles(pe), "ps": _percentiles(ps),
    }.items() if d}


def _price_on(prices: dict, d: date, lookback: int = 7) -> Optional[float]:
    for back in range(lookback + 1):
        p = prices.get(d - timedelta(days=back))
        if p:
            return p
    return None


# ── 基準 ──────────────────────────────────────────────────────────────────

@dataclass
class Baseline:
    ticker: str
    supported: bool
    route: Optional[str] = None          # full / no_gross / ps
    reason: str = ""                     # 不支援或降級的原因(給畫面顯示)
    as_of: Optional[date] = None
    price: Optional[float] = None
    ttm_end: Optional[date] = None
    revenue: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    opex_ratio: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None
    net_margin: Optional[float] = None
    eps_reported: Optional[float] = None     # 財報上的近四季 EPS；含大額業外收益時 eps 會改成本業估算值
    eps_note: str = ""                       # EPS 改用本業估算時的說明
    revenue_growth: Optional[float] = None   # 目前的營收年增率(近四季 vs 再前四季)，已限制在 GROWTH_CLAMP
    growth_note: str = ""                    # 目前年增率遠離歷史範圍時的提醒(基期效應)
    eps: Optional[float] = None
    shares: Optional[float] = None
    pe: Optional[float] = None
    ps: Optional[float] = None
    history: dict = field(default_factory=dict)


def build_baseline(ticker: str, rows: list, price: Optional[float], as_of: date,
                   prices: Optional[dict] = None, excluded_reason: Optional[str] = None) -> Baseline:
    """
    純計算(不碰資料庫、不打網路)，給 compute_baseline 跟測試共用。
    rows：FinancialReportCache 的 dict 列(EPS 已經換到現在的股數基準)。
    """
    if excluded_reason:
        return Baseline(ticker, False, reason=excluded_reason, as_of=as_of)
    series = ttm_series(rows, as_of)
    if not series:
        return Baseline(ticker, False, reason="財報不足連續四季，無法計算近四季基準", as_of=as_of)
    t = series[0]
    b = Baseline(ticker, True, as_of=as_of, price=price, ttm_end=t["end"],
                 revenue=t["revenue"], operating_income=t["operating_income"], net_income=t["net_income"],
                 eps=t["eps"],
                 history=history_distributions(series, prices))
    if not t["revenue"] or t["revenue"] <= 0:
        b.supported, b.reason = False, "還沒有營收，無法用營收或獲利估值"
        return b
    prior = next((p for p in series if 350 <= (t["end"] - p["end"]).days <= 380), None)
    if prior and prior["revenue"] and prior["revenue"] > 0:
        raw = t["revenue"] / prior["revenue"] - 1
        b.revenue_growth = min(max(raw, GROWTH_CLAMP[0]), GROWTH_CLAMP[1])
        d = b.history.get("revenue_growth")
        if d:
            iqr = d["p75"] - d["p25"]
            if raw > d["p75"] + 1.5 * iqr or raw < d["p25"] - 1.5 * iqr:
                side = "高於" if raw > d["p50"] else "低於"
                b.growth_note = (f"目前營收年增率 {raw:.0%}，遠{side}過去 5 年的一般水準（中位數 {d['p50']:.0%}），"
                                 "可能有基期效應，延續這個成長率的假設要小心")
        if raw != b.revenue_growth:
            b.growth_note = (b.growth_note + "；" if b.growth_note else "") + \
                f"目前年增率 {raw:.0%} 超出合理範圍，試算時以 {b.revenue_growth:.0%} 計"
    if t["eps"] and t["net_income"]:
        b.shares = t["net_income"] / t["eps"]
    if t["gross_profit"] is not None:
        b.gross_margin = t["gross_profit"] / t["revenue"]
    if t["operating_income"] is not None:
        b.operating_margin = t["operating_income"] / t["revenue"]
    if b.gross_margin is not None and b.operating_margin is not None:
        b.opex_ratio = b.gross_margin - b.operating_margin
    if t["net_income"] is not None:
        b.net_margin = t["net_income"] / t["revenue"]
    if price and b.shares and b.shares > 0:
        b.ps = price * b.shares / t["revenue"]

    # 含大額業外收益：EPS 改用本業估算 = 營業利益 × 這家公司一般的「淨利 ÷ 營業利益」(稅與業外的常態)
    b.eps_reported = t["eps"]
    oi, ni = t["operating_income"], t["net_income"]
    if t["eps"] and t["eps"] > 0 and oi and oi > 0 and ni and ni > oi * NONOP_RATIO:
        ratios = [x["net_income"] / x["operating_income"] for x in series
                  if x["operating_income"] and x["operating_income"] > 0 and x["net_income"] and x["net_income"] > 0]
        conv = min(max(float(np.median(ratios)), 0.5), 1.0) if ratios else 0.8
        core_ni = oi * conv
        b.eps = t["eps"] * core_ni / ni
        b.net_income = core_ni
        b.net_margin = core_ni / t["revenue"]
        b.eps_note = (f"近四季淨利是營業利益的 {ni / oi:.1f} 倍，含大額業外收益（例如持股增值），"
                      f"EPS 改用本業估算：財報 {t['eps']:.2f} → 本業約 {b.eps:.2f}")

    def use_ps(reason):
        if b.ps:
            b.route, b.reason = "ps", reason + "，改用股價營收比(P/S)估值"
        else:
            b.supported, b.reason = False, reason + "，且股數不足以計算股價營收比"

    # 先看有沒有獲利(EPS)，再看營業利益有沒有申報——虧損公司就算沒申報營業利益也能用 P/S
    if t["eps"] is None:
        b.supported, b.reason = False, "近四季 EPS 資料不完整"
    elif t["eps"] <= 0:
        use_ps("近四季虧損，本益比沒有意義")
    else:
        b.pe = price / b.eps if price else None
        om_ok = b.operating_margin is not None and b.operating_margin >= THIN_MARGIN
        nm_ok = b.net_margin is not None and b.net_margin >= THIN_MARGIN
        if om_ok:
            if b.gross_margin is not None:
                b.route = "full"
            else:
                b.route, b.reason = "no_gross", "沒有毛利資料，跳過毛利率，直接用營業利益率推算"
        elif nm_ok:
            why = "沒有營業利益資料" if b.operating_margin is None else f"營業利益率只有 {b.operating_margin:.1%}，用它推算會被放大"
            b.route, b.reason = "net", why + "，改用淨利率推算(毛利、費用類情境都反映在淨利率上)"
        else:
            use_ps("營業利益率與淨利率都很薄，用獲利比例推算會被極度放大")
    return b


def compute_baseline(ticker: str, as_of: Optional[date] = None) -> Baseline:
    """
    讀 FinancialReportCache + yfinance 股價，組出 build_baseline 需要的資料。
    as_of 不給就是今天(用最新股價)；給了就用 as_of 當天的股價與當時已公告的財報。
    """
    import yfinance as yf
    from apps.calculator.services.implied_assumptions import _non_equity_reason, _split_ratios
    from apps.summaries.models import FinancialReportCache
    from apps.summaries.services.thesis_scenario import financial_sector_tickers

    as_of = as_of or date.today()
    excluded = _non_equity_reason(ticker)
    if not excluded and ticker in financial_sector_tickers():
        excluded = "銀行、壽險、券商的獲利來自利差與投資收益，不適用營收→毛利→EPS"
    if excluded:
        return build_baseline(ticker, [], None, as_of, excluded_reason=excluded)

    rows = list(FinancialReportCache.objects.using("summariesdb").filter(ticker=ticker).values(
        "fiscal_period_end", "disclosure_date", "data_source",
        "revenue", "gross_profit", "operating_income", "net_income", "eps"))
    # FinMind(台股)的 EPS 是當時公告值，不隨分割/減資還原；EDGAR 寫入時已還原過(同 implied_assumptions)
    if any(r["data_source"] == "finmind" for r in rows):
        for sd, ratio in _split_ratios(ticker):
            for r in rows:
                if r["data_source"] == "finmind" and r["eps"] is not None and r["disclosure_date"] \
                        and sd > r["disclosure_date"]:
                    r["eps"] = r["eps"] / ratio

    start = as_of - timedelta(days=int((HISTORY_YEARS + 1.5) * 365.25))
    try:
        # 跟 implied_assumptions 一樣用「已還原分割、未還原股息」的收盤價，本益比才是純的
        hist = yf.Ticker(ticker).history(start=start.isoformat(), end=(as_of + timedelta(days=1)).isoformat(),
                                         interval="1d", auto_adjust=False)["Close"].dropna()
        prices = {ts.date(): float(v) for ts, v in hist.items()}
    except Exception:
        prices = {}
    price = _price_on(prices, as_of) if prices else None
    return build_baseline(ticker, rows, price, as_of, prices=prices)


# ── 情境 → 參數 → 目標價 ───────────────────────────────────────────────────

def default_levers(b: Baseline) -> dict:
    """
    什麼都不勾時的參數：延續目前的營收年增率，毛利率/費用率/本益比維持近四季現值。
    取不到目前年增率(財報不足五季)時退回歷史中位數，再沒有就用 0。
    """
    hist = b.history
    if b.revenue_growth is not None:
        growth = b.revenue_growth
    else:
        growth = hist["revenue_growth"]["p50"] if "revenue_growth" in hist else 0.0
    levers = {
        "revenue_growth": growth,
        "gross_margin": b.gross_margin,
        "opex_ratio": b.opex_ratio,
        "operating_margin": b.operating_margin,
        "net_margin": b.net_margin,
        "pe": b.pe,
        "ps": b.ps,
        "eps_override": None,
        "eps_multiplier": 1.0,
    }
    return levers


def _step(b: Baseline, lever: str, direction: int) -> Optional[float]:
    step = _raw_step(b, lever, direction)
    if lever == "revenue_growth":
        return max(-MAX_GROWTH_STEP, min(step, MAX_GROWTH_STEP))
    current = getattr(b, lever, None)
    if current:
        cap = max(abs(current) * MAX_MARGIN_STEP_RATIO, MIN_STEP.get(lever, 0.0))
        step = max(-cap, min(step, cap))
    return step


def _raw_step(b: Baseline, lever: str, direction: int) -> Optional[float]:
    """
    勾選一個情境時，這個參數要從現值移動多少(有正負號)：加減 (P75−P50) 或 (P50−P25)，
    也就是這家公司過去 5 年這個數字一般的波動幅度。營收成長、各種利潤率都一樣。
    都有最小步幅 MIN_STEP，避免歷史非常平穩的公司勾了情境卻幾乎沒變化；
    上限：營收成長 MAX_GROWTH_STEP，利潤率是現值的 MAX_MARGIN_STEP_RATIO(見 _step)。
    """
    d = b.history.get(lever)
    floor = MIN_STEP.get(lever, 0.0)
    if not d:
        return direction * floor
    gap = (d["p75"] - d["p50"]) if direction > 0 else (d["p50"] - d["p25"])
    return direction * max(gap, floor)


def eps_year_fits(b: Baseline, year, months: int = 12) -> bool:
    """
    節目給的「某年度 EPS」能不能當成試算期間結束時的近四季 EPS：
    試算期間結束時的近四季 = 結束日往前 12 個月，跟那個年度(1/1~12/31)重疊至少半年才算相符。
    例：2025-10 的節目、試算 12 個月 → 近四季是 2025-10~2026-10，2026 年 EPS 相符、2027 年不相符。
    沒有年份的視為「未來一年」，算相符。
    """
    if not year or not b.as_of:
        return True
    end = b.as_of + timedelta(days=round(months * 30.44))
    start = end - timedelta(days=365)
    fy_start, fy_end = date(int(year), 1, 1), date(int(year), 12, 31)
    overlap = (min(end, fy_end) - max(start, fy_start)).days
    return overlap >= 182


def apply_scenarios(b: Baseline, scenarios: list, months: int = 12) -> tuple[dict, list]:
    """
    勾選的情境 → 調整後的參數。回傳(參數, 說明清單)。

    同一個參數、同一個方向的情境不疊加(勾了「需求增加」又勾「新產品」，營收成長只移動一次)，
    方向相反的會互相抵銷一部分。帶數字的情境(EPS 預估、本益比)直接用節目給的數字。
    scenarios 的元素是 thesis_scenario 存下來的情境 dict(至少有 type，可能有 figures)。
    """
    levers = default_levers(b)
    notes = []
    moved = set()          # (參數, 方向)，同方向只動一次

    for s in scenarios:
        t = s.get("type")
        figures = s.get("figures") or {}
        if t == "EPS_FORECAST" and figures.get("eps"):
            if not eps_year_fits(b, figures.get("year"), months):
                notes.append(f"節目給的 EPS {figures['eps']}（{figures.get('year')} 年）跟試算期間不符，沒有直接套用")
                continue
            levers["eps_override"] = float(figures["eps"])
            notes.append(f"EPS 直接使用節目給的 {figures['eps']}" + (f"（{figures.get('year')} 年）" if figures.get("year") else ""))
            continue
        if t in ("RERATING_UP", "RERATING_DOWN") and figures.get("pe"):
            levers["pe"] = float(figures["pe"])
            moved.add(("pe", +1 if t == "RERATING_UP" else -1))
            notes.append(f"本益比使用節目給的 {figures['pe']:g} 倍")
            continue

        for lever, direction in scenario_levers(s).items():
            if (lever, direction) in moved:
                continue
            moved.add((lever, direction))
            if lever == "eps_multiplier":
                levers["eps_multiplier"] *= 1 + PROVISIONAL["FINANCIAL_RISK"]
                notes.append("財務風險：EPS 先暫定打 95 折（尚未校準）")
            elif lever == "pe":
                levers.update(_pe_move(b, levers, direction, t))
                notes.append(_pe_note(b, t, direction))
            elif lever == "revenue_growth":
                levers[lever] += _step(b, lever, direction)
            elif lever == "gross_margin":
                if b.route == "no_gross":
                    # 沒有毛利資料：毛利的變化直接反映在營業利益率上
                    levers["operating_margin"] += _step(b, "operating_margin", direction)
                elif b.route == "net":
                    levers["net_margin"] += _step(b, "net_margin", direction)
                elif levers["gross_margin"] is not None:
                    levers["gross_margin"] += _step(b, lever, direction)
            elif lever == "opex_ratio":
                if b.route == "full" and levers["opex_ratio"] is not None:
                    levers["opex_ratio"] += _step(b, lever, direction)
                elif b.route == "no_gross":
                    levers["operating_margin"] -= _step(b, "operating_margin", direction)
                elif b.route == "net":
                    levers["net_margin"] -= _step(b, "net_margin", direction)
    return levers, notes


def _pe_move(b: Baseline, levers: dict, direction: int, scenario_type: str) -> dict:
    """
    本益比(虧損公司則是 P/S)跟其他參數一樣以現值為基準：現值加減這家公司本益比一般的波動幅度
    (P75−P50 或 P50−P25)，最少現值的 5%、最多 25%；沒有歷史時固定調 10%。
    總經、法規情境只移動一半(暫定)。
    (之前是往歷史中位數/P75 移一個級距，現值遠低於歷史時會跳太多：Netflix 21.6 → 39.5 倍。)
    """
    key = "ps" if b.route == "ps" else "pe"
    cur = levers.get(key)
    if not cur:
        return {}
    d = b.history.get(key)
    if d:
        gap = (d["p75"] - d["p50"]) if direction > 0 else (d["p50"] - d["p25"])
        step = min(max(gap, cur * MULTIPLE_STEP[0]), cur * MULTIPLE_STEP[1])
    else:
        step = cur * 0.10
    share = PROVISIONAL["MACRO"] if scenario_type.startswith("MACRO") or scenario_type == "REGULATORY_RISK" else 1.0
    return {key: cur + direction * step * share}


def _pe_note(b: Baseline, scenario_type: str, direction: int) -> str:
    name = "股價營收比" if b.route == "ps" else "本益比"
    side = "調高" if direction > 0 else "調低"
    if scenario_type.startswith("MACRO"):
        return f"總經情境：{name}從現值{side}半個一般波動幅度（暫定）"
    if scenario_type == "REGULATORY_RISK":
        return f"法規風險：{name}從現值{side}半個一般波動幅度（暫定）"
    return f"{name}從現值{side}一個一般波動幅度"


@dataclass
class Projection:
    route: str
    months: int                          # 試算期間(月)
    revenue: Optional[float]
    operating_income: Optional[float]
    eps: Optional[float]
    multiple: Optional[float]            # 本益比或 P/S
    target_price: Optional[float]
    implied_return: Optional[float]
    levers: dict
    notes: list
    revenue_factor: Optional[float] = None   # 預估營收 ÷ 近四季營收(畫面顯示計算過程用)
    profit_factor: Optional[float] = None    # 預估獲利 ÷ 近四季獲利(EPS 就是近四季 EPS × 這個倍數)
    margin_share: float = 1.0                # 利潤率變化反映到這段期間的比例(見 project 說明)


def project(b: Baseline, levers: dict, notes: Optional[list] = None, months: int = 12) -> Optional[Projection]:
    """
    參數 → 試算期間結束時(months 個月後)的近四季營收、營業利益、EPS、目標價。

    試算期間依節目回測期間(例如「未來幾個季度」→ 6 個月)，不是固定 12 個月：
      營收：營收成長率是「年增率」，m 個月後的近四季營收 = 近四季營收 × (1 + 年增率)^(m/12)
      利潤率：情境讓利潤率改變多少是以一年計，m 個月後的近四季只有 m/12 的部分反映新利潤率
              (最多 100%)，所以利潤率的變化乘上 min(1, m/12)
      本益比：市場隨時會重新定價，不依期間縮放
    什麼都不調時：目標價 = 發布日股價 × (1 + 目前年增率)^(m/12)。
      full：    營業利益 = 營收 × (毛利率 − 費用率)
      no_gross：營業利益 = 營收 × 營業利益率
      net：     淨利 = 營收 × 淨利率，EPS = 近四季EPS × 淨利 ÷ 近四季淨利
      EPS = 近四季EPS × 營業利益 ÷ 近四季營業利益 × eps_multiplier(財務風險)
            有 eps_override(節目給的EPS)時直接用它
      目標價 = EPS × 本益比
      ps：      目標價 = 預估營收 × P/S ÷ 股數(虧損公司不算 EPS)
    """
    if not b.supported or not b.route:
        return None
    notes = list(notes or [])
    months = max(1, int(months))
    share = min(1.0, months / 12)
    revenue_factor = max(1 + levers["revenue_growth"], 0.0) ** (months / 12)
    revenue = b.revenue * revenue_factor

    def blended(key, base):
        return base + (levers[key] - base) * share

    if b.route == "ps":
        multiple = levers.get("ps")
        target = revenue * multiple / b.shares if multiple and b.shares else None
        return Projection("ps", months, revenue, None, None, multiple, target,
                          _ret(target, b.price), levers, notes, revenue_factor, None, share)

    if b.route == "net":
        operating_income = None
        net_income = revenue * blended("net_margin", b.net_margin)
        base_profit, profit = b.net_income, net_income
    else:
        if b.route == "full":
            margin = blended("gross_margin", b.gross_margin) - blended("opex_ratio", b.opex_ratio)
        else:
            margin = blended("operating_margin", b.operating_margin)
        operating_income = revenue * margin
        base_profit, profit = b.operating_income, operating_income
    if levers.get("eps_override"):
        eps = levers["eps_override"]
    else:
        eps = b.eps * profit / base_profit
    eps *= levers.get("eps_multiplier", 1.0)
    multiple = levers.get("pe")
    target = eps * multiple if multiple and eps > 0 else None
    if eps <= 0:
        notes.append("推算的 EPS 為負，本益比估值不適用")
    return Projection(b.route, months, revenue, operating_income, eps, multiple, target,
                      _ret(target, b.price), levers, notes, revenue_factor, profit / base_profit, share)


def _ret(target: Optional[float], price: Optional[float]) -> Optional[float]:
    return target / price - 1 if target and price else None


def project_scenarios(b: Baseline, scenarios: list, months: int = 12) -> Optional[Projection]:
    """一步到位：勾選的情境 → Projection。"""
    levers, notes = apply_scenarios(b, scenarios, months)
    return project(b, levers, notes, months)


def waterfall(b: Baseline, scenarios: list, labels: list, overrides: Optional[dict] = None,
              months: int = 12) -> Optional[list]:
    """
    瀑布圖：從發布日股價開始，一步一步加上去，看每個情境各把目標價推高或拉低多少。
      1. 延續目前成長：什麼都不勾的目標價 − 發布日股價(營收照目前年增率、其他維持現值)
      2. 依勾選順序逐一加入情境：加入後的目標價 − 加入前的目標價
         (同方向的情境不疊加，所以後面重複的情境會是 0，畫面會說明)
      3. 手動調整：套用使用者拉過的滑桿/指定的 EPS 後的差額
    每一步的金額加總 = 最終目標價 − 發布日股價。任何一步算不出目標價(例如 EPS 變負)就回傳 None。

    scenarios：要加入的情境 dict(依勾選順序)；labels：每個情境在圖上的名稱，長度跟 scenarios 相同。
    overrides：使用者手動覆寫的參數(key 同 levers，eps 另外用 "eps_override")。
    """
    if not b.supported or not b.price:
        return None

    def target_of(chosen, extra=None):
        levers, notes = apply_scenarios(b, chosen, months)
        if extra:
            levers.update(extra)
        p = project(b, levers, notes, months)
        return p.target_price if p else None

    steps = []
    prev = b.price
    base = target_of([])
    if base is None:
        return None
    steps.append({"label": "延續目前成長", "kind": "trend", "delta": base - prev, "value": base})
    prev = base
    for k, label in enumerate(labels, start=1):
        cur = target_of(scenarios[:k])
        if cur is None:
            return None
        steps.append({"label": label, "kind": "scenario", "delta": cur - prev, "value": cur})
        prev = cur
    if overrides:
        cur = target_of(scenarios, overrides)
        if cur is None:
            return None
        steps.append({"label": "手動調整", "kind": "manual", "delta": cur - prev, "value": cur})
    return steps
