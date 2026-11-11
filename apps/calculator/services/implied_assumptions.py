# apps/calculator/services/implied_assumptions.py
"""
「引擎隱含的基本面假設」——組合一(成長×估值)的步驟一。

目的：拔靴模擬抽的是這檔股票過去N年的真實每日報酬，所以基準情境本來就「帶著」那N年的
EPS成長與估值(P/E)擴張。這裡把它拆出來：

    股價 = EPS × P/E   →   ln(價格倍數) = ln(EPS倍數) + ln(P/E倍數)

    μ   : 窗口內平均每年對數報酬(價格，已還原股息)
    g₀  : 窗口內EPS年化成長率，用窗口起點與終點各一個「近四季EPS(TTM)」算
    ρ₀  : 估值擴張(含股息再投入與其他) = μ − ln(1+g₀)，用「剩餘」算，不需要歷史P/E

只用「當時已公告」的財報(disclosure_date <= 日期)，避免偷看未來。純計算、不寫資料庫，
目前沒有接到任何API或畫面，是給後續「成長／估值滑桿預設值」用的資料準備。
"""
from datetime import date, timedelta
from typing import Optional

import numpy as np

MIN_TTM_EPS = 0.0   # TTM EPS <= 這個值時，成長率沒有意義，直接回報「不支援」


def _split_ratios(ticker: str) -> list:
    """
    yfinance 的分割／配股／減資紀錄：[(生效日, 比例)]。股價已經被 yfinance 依這些紀錄還原到
    「今天的股數基準」，所以 EPS 也必須用同一份紀錄還原，兩邊才在同一個基準上，不然
    (股價 ÷ EPS)的本益比與期初到期末的EPS成長都會失真。比例太離譜的視為資料錯誤略過。
    """
    import yfinance as yf

    try:
        sp = yf.Ticker(ticker).splits
    except Exception:
        return []
    out = []
    for ts, ratio in sp.items():
        r = float(ratio)
        if r > 0 and r != 1 and 0.05 <= r <= 60:
            out.append((ts.date(), r))
    return out


def _non_equity_reason(ticker: str) -> Optional[str]:
    """
    指數、商品、加密貨幣、ETF 本身沒有每股盈餘(EPS)，「股價＝EPS×P/E」拆不出來，
    跟「財報資料不足」是不同原因，分開說明，使用者才知道補資料也沒用。
    只用代號字面判斷，不打網路；美股 ETF 另外在沒有財報時才用 yfinance 查類型。
    """
    t = ticker.upper()
    if t.startswith("^"):
        return "指數沒有每股盈餘，不適用基本面假設"
    if "=" in t:
        return "商品／期貨沒有每股盈餘，不適用基本面假設"
    if t.endswith("-USD"):
        return "加密貨幣沒有每股盈餘，不適用基本面假設"
    code = t.split(".")[0]
    if t.endswith((".TW", ".TWO")) and code.startswith("00"):
        return "ETF 沒有自己的每股盈餘，不適用基本面假設"
    return None


def _is_etf_by_yfinance(ticker: str) -> bool:
    import yfinance as yf

    try:
        qt = (yf.Ticker(ticker).get_info() or {}).get("quoteType", "")
    except Exception:
        return False
    return qt in ("ETF", "MUTUALFUND")


def _ttm_eps(rows: list, as_of: date) -> Optional[float]:
    """as_of 當時已公告的最近四季 EPS 合計；不足四季回傳 None。"""
    avail = [r for r in rows if r["disclosure_date"] and r["disclosure_date"] <= as_of and r["eps"] is not None]
    avail.sort(key=lambda r: r["fiscal_period_end"], reverse=True)
    if len(avail) < 4:
        return None
    last4 = avail[:4]
    # 四季必須是連續的(相鄰兩期間隔約3個月，上限放寬到120天)，不然中間缺一期會把年度算錯
    ends = [r["fiscal_period_end"] for r in last4]
    for a, b in zip(ends, ends[1:]):
        if not 70 <= (a - b).days <= 120:   # 52/53週制公司(如Costco)的Q4是16週，約112天
            return None
    return float(sum(r["eps"] for r in last4))


def compute_implied_assumptions(ticker: str, as_of_date: Optional[date] = None, years: int = 5) -> dict:
    """
    回傳 dict：
      supported: bool，False 時看 reason
      mu, g0, rho0 : 年化(對數)的 μ、ln(1+g₀)、ρ₀，另有 g0_simple(一般百分比的EPS年化成長)
      pe_start, pe_end, pe_pace : 窗口起點／終點的本益比，以及年化變化(一般百分比)；取不到時是 None
      eps_start, eps_end, window_start, window_end, years_used
    """
    import yfinance as yf
    from apps.summaries.models import FinancialReportCache

    reason = _non_equity_reason(ticker)
    if reason:
        return {"supported": False, "reason": reason}

    end = as_of_date or date.today()
    start = end - timedelta(days=int(years * 365.25))

    prices = yf.Ticker(ticker).history(
        start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), interval="1d", auto_adjust=True,
    )["Close"].dropna()
    if len(prices) < 250:
        return {"supported": False, "reason": "價格資料不足"}

    first_day = prices.index[0].date()
    last_day = prices.index[-1].date()
    years_used = (last_day - first_day).days / 365.25
    if years_used < 1:
        return {"supported": False, "reason": "窗口太短"}

    # 價格端：窗口首尾的對數報酬，年化(已還原股息)
    mu = float(np.log(prices.iloc[-1] / prices.iloc[0]) / years_used)

    rows = list(
        FinancialReportCache.objects.using("summariesdb")
        .filter(ticker=ticker).values("fiscal_period_end", "disclosure_date", "eps", "data_source")
    )
    # FinMind(台股)存的是「當時公告」的EPS，不會隨分割／配股／減資還原；EDGAR那份在寫入時就已
    # 用同一份分割紀錄還原過，這裡只處理 FinMind 來源的列，避免重複還原。
    if any(r["data_source"] == "finmind" for r in rows):
        ratios = _split_ratios(ticker)
        if ratios:
            for r in rows:
                if r["data_source"] == "finmind" and r["eps"] is not None and r["disclosure_date"]:
                    for sd, ratio in ratios:
                        if sd > r["disclosure_date"]:
                            r["eps"] = r["eps"] / ratio
    if not rows:
        if _is_etf_by_yfinance(ticker):
            return {"supported": False, "reason": "ETF 沒有自己的每股盈餘，不適用基本面假設"}
        return {"supported": False, "reason": "沒有財報資料"}

    eps_start = _ttm_eps(rows, first_day)
    eps_end = _ttm_eps(rows, last_day)
    if eps_start is None or eps_end is None:
        return {"supported": False, "reason": "窗口起點或終點的近四季EPS資料不足"}
    if eps_start <= MIN_TTM_EPS or eps_end <= MIN_TTM_EPS:
        return {"supported": False, "reason": "EPS為負或接近0，成長率沒有意義"}

    g_log = float(np.log(eps_end / eps_start) / years_used)      # ln(1+g₀)
    rho0 = mu - g_log

    # 真正的本益比：用「未還原股息、但已還原分割」的收盤價 ÷ 當時已公告的近四季EPS。
    # 股息再投入只出現在還原後的價格裡，所以這裡的本益比是純的，不含股息；情境與基準
    # 之間股息的部分相同，會互相抵消，不影響倍數F。
    pe_start = pe_end = pe_pace = div_log = None
    try:
        raw = yf.Ticker(ticker).history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), interval="1d", auto_adjust=False,
        )["Close"].dropna()
        if len(raw) >= 2:
            p0, p1 = float(raw.iloc[0]), float(raw.iloc[-1])
            if p0 > 0 and p1 > 0:
                pe_start, pe_end = p0 / eps_start, p1 / eps_end
                pe_pace = float((pe_end / pe_start) ** (1 / years_used) - 1)
                # 還原股息的價格比沒還原的多漲的那一塊，年化(對數)＝股息再投入的貢獻
                div_log = float(mu - np.log(p1 / p0) / years_used)
    except Exception:
        pe_start = pe_end = pe_pace = div_log = None
    return {
        "supported": True,
        "mu": mu,
        "g0_log": g_log,
        "g0_simple": float(np.exp(g_log) - 1),
        "rho0": rho0,
        "pe_start": pe_start,
        "pe_end": pe_end,
        "pe_pace": pe_pace,
        "div_log": div_log,
        "eps_start": eps_start,
        "eps_end": eps_end,
        "window_start": first_day,
        "window_end": last_day,
        "years_used": years_used,
    }
