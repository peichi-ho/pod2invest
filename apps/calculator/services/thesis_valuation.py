# apps/calculator/services/thesis_valuation.py
"""
論點情境試算的第二步：每家公司的「基準數字」，以及把勾選的情境推算成 EPS 與目標價。

流程(第一步見 apps/summaries/services/thesis_scenario.py)：
    驗證句 → 情境(需求↑、漲價、毛利改善…) → 財務參數(營收成長、毛利率、費用率、本益比)
           → 本檔：近四季基準 × 調整 → 未來12個月 EPS → × 本益比 → 目標價

三個原則：
  1. 基準一律用「當時已公告」的近四季財報(disclosure_date <= as_of)，回測舊節目時不偷看未來。
  2. 情境的預設調整幅度來自這家公司自己過去約5年的分布(第25/50/75百分位)，不是自訂常數；
     少數還沒有歷史可依的(財務風險、總經)用暫定值，並在結果裡標 provisional=True。
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
}


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

    # 先看有沒有獲利(EPS)，再看營業利益有沒有申報——虧損公司就算沒申報營業利益也能用 P/S
    if t["eps"] is None:
        b.supported, b.reason = False, "近四季 EPS 資料不完整"
    elif t["eps"] <= 0:
        if b.ps:
            b.route, b.reason = "ps", "近四季虧損，本益比沒有意義，改用股價營收比(P/S)估值"
        else:
            b.supported, b.reason = False, "近四季虧損，且股數不足以計算股價營收比"
    else:
        b.pe = price / t["eps"] if price else None
        if t["operating_income"] is not None and t["operating_income"] > 0:
            if b.gross_margin is not None:
                b.route = "full"
            else:
                b.route, b.reason = "no_gross", "沒有毛利資料，跳過毛利率，直接用營業利益率推算"
        elif b.net_margin and b.net_margin > 0:
            b.route, b.reason = "net", "沒有營業利益資料，改用淨利率推算(毛利、費用類情境都反映在淨利率上)"
        else:
            b.supported, b.reason = False, "近四季營業利益與淨利都不足以推算"
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
    Base 情境(什麼都不勾)的參數：營收成長用歷史中位數(P50)，毛利率/費用率/本益比用近四季現值。
    歷史不足時營收成長用 0，並在 notes 說明。
    """
    hist = b.history
    levers = {
        "revenue_growth": hist["revenue_growth"]["p50"] if "revenue_growth" in hist else 0.0,
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
    current = getattr(b, lever, None)
    if lever != "revenue_growth" and current:
        cap = max(abs(current) * MAX_MARGIN_STEP_RATIO, MIN_STEP.get(lever, 0.0))
        step = max(-cap, min(step, cap))
    return step


def _raw_step(b: Baseline, lever: str, direction: int) -> Optional[float]:
    """
    勾選一個情境時，這個參數要移動多少(有正負號)。來源：
      revenue_growth：往上移到 P75、往下移到 P25(相對 Base 的 P50)
      gross_margin / opex_ratio / operating_margin：加減 (P75−P50) 或 (P50−P25)，即公司自己一般的波動幅度
    兩種都有最小步幅 MIN_STEP，避免歷史非常平穩的公司勾了情境卻幾乎沒變化；
    利潤率類另有上限 MAX_MARGIN_STEP_RATIO(見 _step)。
    """
    d = b.history.get(lever)
    floor = MIN_STEP.get(lever, 0.0)
    if not d:
        return direction * floor
    gap = (d["p75"] - d["p50"]) if direction > 0 else (d["p50"] - d["p25"])
    return direction * max(gap, floor)


def apply_scenarios(b: Baseline, scenarios: list) -> tuple[dict, list]:
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
            levers["eps_override"] = float(figures["eps"])
            notes.append(f"EPS 直接使用節目給的 {figures['eps']}" + (f"（{figures.get('year')} 年）" if figures.get("year") else ""))
            continue
        if t in ("RERATING_UP", "RERATING_DOWN") and figures.get("pe"):
            levers["pe"] = float(figures["pe"])
            moved.add(("pe", +1 if t == "RERATING_UP" else -1))
            notes.append(f"本益比使用節目給的 {figures['pe']:g} 倍")
            continue

        for lever, direction in LEVERS.get(t, {}).items():
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
    本益比(虧損公司則是 P/S)往歷史分布移動「一個級距」：
      估值偏低：現值低於中位數 → 修復到中位數；介於中位數與 P75 → 到 P75；已經高於 P75 → 不動
      估值偏高：反過來，往中位數或 P25 移動
    一開始是直接跳到 P75/P25，但現值遠低於歷史時會跳太多(Netflix 本益比 21.6 → 46.6 倍，目標價 +192%)。
    總經情境只移動一半(暫定)。
    """
    key = "ps" if b.route == "ps" else "pe"
    cur = levers.get(key)
    d = b.history.get(key)
    if cur is None or not d:
        return {}
    if direction > 0:
        target = d["p50"] if cur < d["p50"] else max(cur, d["p75"])
    else:
        target = d["p50"] if cur > d["p50"] else min(cur, d["p25"])
    share = PROVISIONAL["MACRO"] if scenario_type.startswith("MACRO") else 1.0
    return {key: cur + (target - cur) * share}


def _pe_note(b: Baseline, scenario_type: str, direction: int) -> str:
    name = "股價營收比" if b.route == "ps" else "本益比"
    side = "中位數或 P75" if direction > 0 else "中位數或 P25"
    if scenario_type.startswith("MACRO"):
        return f"總經情境：{name}往歷史{side}移動一半（暫定）"
    return f"{name}往歷史分布移動一個級距（{side}）"


@dataclass
class Projection:
    route: str
    revenue: Optional[float]
    operating_income: Optional[float]
    eps: Optional[float]
    multiple: Optional[float]            # 本益比或 P/S
    target_price: Optional[float]
    implied_return: Optional[float]
    levers: dict
    notes: list


def project(b: Baseline, levers: dict, notes: Optional[list] = None) -> Optional[Projection]:
    """
    參數 → 未來12個月的營收、營業利益、EPS、目標價。
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
    revenue = b.revenue * (1 + levers["revenue_growth"])

    if b.route == "ps":
        multiple = levers.get("ps")
        target = revenue * multiple / b.shares if multiple and b.shares else None
        return Projection("ps", revenue, None, None, multiple, target,
                          _ret(target, b.price), levers, notes)

    if b.route == "net":
        operating_income = None
        net_income = revenue * levers["net_margin"]
        base_profit, profit = b.net_income, net_income
    else:
        margin = (levers["gross_margin"] - levers["opex_ratio"]) if b.route == "full" else levers["operating_margin"]
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
    return Projection(b.route, revenue, operating_income, eps, multiple, target,
                      _ret(target, b.price), levers, notes)


def _ret(target: Optional[float], price: Optional[float]) -> Optional[float]:
    return target / price - 1 if target and price else None


def project_scenarios(b: Baseline, scenarios: list) -> Optional[Projection]:
    """一步到位：勾選的情境 → Projection。"""
    levers, notes = apply_scenarios(b, scenarios)
    return project(b, levers, notes)


def waterfall(b: Baseline, scenarios: list, labels: list, overrides: Optional[dict] = None) -> Optional[list]:
    """
    瀑布圖：從發布日股價開始，一步一步加上去，看每個情境各把目標價推高或拉低多少。
      1. 公司一般趨勢：什麼都不勾(Base)的目標價 − 發布日股價
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
        levers, notes = apply_scenarios(b, chosen)
        if extra:
            levers.update(extra)
        p = project(b, levers, notes)
        return p.target_price if p else None

    steps = []
    prev = b.price
    base = target_of([])
    if base is None:
        return None
    steps.append({"label": "公司一般趨勢", "kind": "trend", "delta": base - prev, "value": base})
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
