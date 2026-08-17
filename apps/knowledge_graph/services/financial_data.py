# apps/knowledge_graph/services/financial_data.py
"""
Layer 2 財務基本面資料擷取層。

ticker 解析在這裡獨立重新實作，不沿用 apps.summaries 那一套規則——那邊對
純數字代號一律假設是「.TW」（上市），對上櫃股票（例如環球晶 6488 實際是
「.TWO」）會解析成錯誤代碼，而且 yfinance 對錯誤代碼通常不會報錯，只會
默默回傳全部是 NaN 的財報，非常難察覺。這裡改成用 yfinance 實際查詢驗證
存在性，不是憑代號形狀猜後綴。

資料來源優先序：
  1. yfinance —— 台股、外國股通用同一套欄位名稱，且能直接拿到股數、EBIT、
     實際財報公告日期（t.earnings_dates），是首選。
  2. FinMind —— 只有台股資料，但歷史回溯較長；沒有 EBIT/股數/公告日欄位，
     這裡用近似值替代（見下方註解）。
  3. 兩邊都拿不到 —— 誠實回傳 data_source=None，由呼叫端標記「無法評估」，
     不強行拼湊。

同一期（同一 ticker、同一 fiscal_period_end）的所有欄位一定只來自其中一個
來源，不會把 yfinance 的分子跟 FinMind 的分母湊在一起算比率——兩邊對同一
會計項目的認列範圍不一定完全一致，混用會做出「看起來正常、實際上分子分母
基準不一致」的比率，比完全拿不到資料更危險。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import requests

from apps.knowledge_graph.models import FinancialMetricsCache

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

# F-score 計算需要的最小欄位集合。任一項缺失，這一期就視為不完整，
# 整期改用下一個資料來源重抓，不用其他來源的欄位去補這一期的缺口。
REQUIRED_FIELDS = [
    "total_assets", "current_assets", "current_liabilities", "total_liabilities",
    "working_capital", "retained_earnings", "ebit", "revenue", "gross_profit",
    "operating_income", "net_income", "operating_cash_flow", "shares_outstanding", "eps",
]

_NUMERIC_RE = re.compile(r"^\d{4,6}$")
_LETTER_RE = re.compile(r"^[A-Z]{1,6}$")

# 借用的 apps.summaries resolve_ticker() 對「原油」「黃金」這類巨觀名詞會解析成
# 期貨/指數/外匯代號（例如 "CL=F"），這裡代表的是一個真實可投資的「公司」，
# 這幾類都不是公司，實測抽樣圖譜節點時發現「原油供應」被解析成 CL=F 才發現
# 這裡也需要跟 basket_selection.py 的 filter_node_type 一樣的過濾。
_NON_COMPANY_TICKER_RE = re.compile(r"^\^|=F$|=X$")
_KNOWN_NON_COMPANY_TICKERS = {"BTC-USD"}


def _is_non_company_ticker(ticker: str) -> bool:
    return bool(_NON_COMPANY_TICKER_RE.search(ticker)) or ticker in _KNOWN_NON_COMPANY_TICKERS


# ── ticker 解析 ──────────────────────────────────────────────────────────

def _ticker_exists(ticker: str) -> bool:
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d")
        return not hist.empty
    except Exception:
        return False


def resolve_financial_ticker(name: str) -> str | None:
    """
    節點名稱 → yfinance 格式 ticker（例如 "2330.TW"、"NVDA"）。

    純數字代號、純大寫英文字母：直接規則判斷 + 實際查詢驗證是否存在，
    不憑代號形狀猜後綴。

    中文公司名（KG 節點的主要形式，例如「台積電」）沒有現成的規則可以直接
    判斷 ticker，這裡借用既有的 apps.summaries 名稱對照表（TickerMap）當
    起點，但不直接信任它回傳的結果——那正是環球晶 .TW/.TWO 判斷錯誤的
    來源。改成一定要通過 yfinance 存在性驗證，驗證失敗且看起來是台股代號
    時，嘗試把 .TW/.TWO 後綴互換再驗證一次，兩邊都不通過才回傳 None。
    """
    name = name.strip()
    if _NUMERIC_RE.match(name):
        for suffix in (".TW", ".TWO"):
            candidate = f"{name}{suffix}"
            if _ticker_exists(candidate):
                return candidate
        return None
    if _LETTER_RE.match(name):
        return name if _ticker_exists(name) else None

    try:
        from apps.summaries.services.backtesting import resolve_ticker as _legacy_resolve_ticker
        candidate = _legacy_resolve_ticker(name)
    except Exception:
        candidate = ""
    if not candidate or _is_non_company_ticker(candidate):
        return None
    if _ticker_exists(candidate):
        return candidate

    for old, new in ((".TW", ".TWO"), (".TWO", ".TW")):
        if candidate.endswith(old):
            swapped = candidate[: -len(old)] + new
            if _ticker_exists(swapped):
                return swapped
    return None


def _finmind_stock_id(ticker: str) -> str | None:
    """yfinance 格式台股 ticker → FinMind 需要的純數字代號；非台股回傳 None。"""
    for suffix in (".TW", ".TWO"):
        if ticker.endswith(suffix):
            return ticker[: -len(suffix)]
    return None


# ── 公告延遲假設（僅在 yfinance 抓不到實際公告日時的備援） ───────────────────

def _assumed_disclosure_lag_days(period_end: date) -> int:
    """
    台灣規定 Q1/Q2/Q3 季報約 45 天內須公告，年報（Q4，須經會計師簽證）
    約 3 個月。用期末月份判斷是哪一種。
    """
    return 90 if period_end.month == 12 else 45


# ── yfinance ─────────────────────────────────────────────────────────────

def _extract_yf_period(bs, inc, cf, period_end) -> dict | None:
    import pandas as pd

    def g(df, row_name):
        try:
            v = df.loc[row_name, period_end]
            return None if pd.isna(v) else float(v)
        except Exception:
            return None

    current_assets = g(bs, "Current Assets")
    current_liabilities = g(bs, "Current Liabilities")
    working_capital = g(bs, "Working Capital")
    if working_capital is None and current_assets is not None and current_liabilities is not None:
        working_capital = current_assets - current_liabilities

    total_liabilities = g(bs, "Total Liabilities Net Minority Interest")

    return {
        "total_assets": g(bs, "Total Assets"),
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "total_liabilities": total_liabilities,
        "working_capital": working_capital,
        "retained_earnings": g(bs, "Retained Earnings"),
        "ebit": g(inc, "EBIT"),
        "revenue": g(inc, "Total Revenue"),
        "gross_profit": g(inc, "Gross Profit"),
        "operating_income": g(inc, "Operating Income"),
        "net_income": g(inc, "Net Income"),
        "operating_cash_flow": g(cf, "Operating Cash Flow"),
        "shares_outstanding": g(bs, "Ordinary Shares Number"),
        "eps": g(inc, "Diluted EPS") or g(inc, "Basic EPS"),
    }


def _nearest_disclosure_date(earnings_dates, period_end: date) -> date | None:
    """
    t.earnings_dates 是「公告日 → 財報內容」的表，用期末日往後找最近一筆
    公告（財報公告一定晚於期末日，不會提早）。
    """
    if earnings_dates is None or earnings_dates.empty:
        return None
    try:
        candidates = [d.date() for d in earnings_dates.index if d.date() >= period_end]
        return min(candidates) if candidates else None
    except Exception:
        return None


def _fetch_yfinance_periods(ticker: str) -> list[dict]:
    import yfinance as yf

    t = yf.Ticker(ticker)
    try:
        bs, inc, cf = t.quarterly_balance_sheet, t.quarterly_income_stmt, t.quarterly_cashflow
    except Exception:
        return []
    if bs.empty or inc.empty:
        return []

    try:
        earnings_dates = t.earnings_dates
    except Exception:
        earnings_dates = None

    periods = []
    for period_end_ts in bs.columns:
        period_end = period_end_ts.date()
        fields = _extract_yf_period(bs, inc, cf, period_end_ts)
        if any(fields.get(f) is None for f in REQUIRED_FIELDS if f != "total_liabilities"):
            continue  # 這一期資料不完整，整期跳過（不跨來源補欄位）
        if fields.get("total_liabilities") is None and fields.get("current_liabilities") is not None:
            # Total Liabilities Net Minority Interest 偶爾缺，退回用歷史欄位近似（流動+非流動）
            continue

        disclosure_date = _nearest_disclosure_date(earnings_dates, period_end)
        if disclosure_date is None:
            disclosure_date = period_end + timedelta(days=_assumed_disclosure_lag_days(period_end))

        periods.append({
            "fiscal_period_end": period_end,
            "disclosure_date": disclosure_date,
            "data_source": "yfinance",
            **fields,
        })

    periods.sort(key=lambda p: p["fiscal_period_end"], reverse=True)
    return periods


def _fetch_yfinance_annual_revenue(ticker: str) -> list[dict]:
    import yfinance as yf
    import pandas as pd

    t = yf.Ticker(ticker)
    try:
        inc = t.income_stmt
    except Exception:
        return []
    if inc.empty or "Total Revenue" not in inc.index:
        return []

    out = []
    for col in inc.columns:
        v = inc.loc["Total Revenue", col]
        if pd.isna(v):
            continue
        out.append({"year": col.date().year, "revenue": float(v)})
    out.sort(key=lambda r: r["year"], reverse=True)
    return out


# ── FinMind（fallback，僅台股） ──────────────────────────────────────────

def _finmind_get(dataset: str, stock_id: str, start_date: str, end_date: str) -> list[dict]:
    try:
        resp = requests.get(
            FINMIND_URL,
            params={"dataset": dataset, "data_id": stock_id, "start_date": start_date, "end_date": end_date},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception:
        return []


def _pivot_finmind_statement(rows: list[dict]) -> dict[date, dict[str, float]]:
    by_period: dict[date, dict[str, float]] = {}
    for row in rows:
        try:
            period_end = date.fromisoformat(row["date"])
        except Exception:
            continue
        by_period.setdefault(period_end, {})[row["type"]] = row["value"]
    return by_period


def _fetch_finmind_periods(ticker: str, start_date: date, end_date: date) -> list[dict]:
    stock_id = _finmind_stock_id(ticker)
    if not stock_id:
        return []  # 非台股，FinMind 一定沒有，不用浪費呼叫

    s, e = start_date.isoformat(), end_date.isoformat()
    bs = _pivot_finmind_statement(_finmind_get("TaiwanStockBalanceSheet", stock_id, s, e))
    inc = _pivot_finmind_statement(_finmind_get("TaiwanStockFinancialStatements", stock_id, s, e))
    cf = _pivot_finmind_statement(_finmind_get("TaiwanStockCashFlowsStatement", stock_id, s, e))
    shareholding = _finmind_get("TaiwanStockShareholding", stock_id, s, e)
    shares_by_date = {
        date.fromisoformat(r["date"]): r["NumberOfSharesIssued"]
        for r in shareholding if r.get("NumberOfSharesIssued")
    }

    periods = []
    for period_end, bs_row in bs.items():
        inc_row = inc.get(period_end, {})
        cf_row = cf.get(period_end, {})
        if not inc_row:
            continue

        total_liabilities = None
        cur_liab = bs_row.get("CurrentLiabilities")
        noncur_liab = bs_row.get("NoncurrentLiabilities")
        if cur_liab is not None and noncur_liab is not None:
            total_liabilities = cur_liab + noncur_liab

        working_capital = None
        cur_assets = bs_row.get("CurrentAssets")
        if cur_assets is not None and cur_liab is not None:
            working_capital = cur_assets - cur_liab

        # FinMind 沒有 EBIT 欄位，用「營業利益」近似（業界常見簡化，非精確定義）。
        ebit = inc_row.get("OperatingIncome")

        # 股數：FinMind 的 Shareholding 是逐日資料，找期末日之後最近一筆。
        shares_outstanding = None
        future_dates = sorted(d for d in shares_by_date if d >= period_end)
        if future_dates:
            shares_outstanding = shares_by_date[future_dates[0]]

        fields = {
            "total_assets": bs_row.get("TotalAssets"),
            "current_assets": cur_assets,
            "current_liabilities": cur_liab,
            "total_liabilities": total_liabilities,
            "working_capital": working_capital,
            "retained_earnings": bs_row.get("RetainedEarnings"),
            "ebit": ebit,
            "revenue": inc_row.get("Revenue"),
            "gross_profit": inc_row.get("GrossProfit"),
            "operating_income": inc_row.get("OperatingIncome"),
            "net_income": inc_row.get("IncomeAfterTaxes"),
            "operating_cash_flow": cf_row.get("CashFlowsFromOperatingActivities")
                or cf_row.get("NetCashInflowFromOperatingActivities"),
            "shares_outstanding": shares_outstanding,
            "eps": inc_row.get("EPS"),
        }
        if any(fields.get(f) is None for f in REQUIRED_FIELDS):
            continue  # 不完整，跳過（FinMind 已經是最後一層，這一期直接視為抓不到）

        periods.append({
            "fiscal_period_end": period_end,
            # FinMind 沒有公告日欄位，用假設延遲天數。
            "disclosure_date": period_end + timedelta(days=_assumed_disclosure_lag_days(period_end)),
            "data_source": "finmind",
            **fields,
        })

    periods.sort(key=lambda p: p["fiscal_period_end"], reverse=True)
    return periods


# ── 快取 ─────────────────────────────────────────────────────────────────

def _load_cached(ticker: str) -> list[dict]:
    rows = FinancialMetricsCache.objects.using("knowledge_graphdb").filter(ticker=ticker)
    out = []
    for r in rows:
        out.append({
            "fiscal_period_end": r.fiscal_period_end,
            "disclosure_date": r.disclosure_date,
            "data_source": r.data_source,
            "total_assets": r.total_assets, "current_assets": r.current_assets,
            "current_liabilities": r.current_liabilities, "total_liabilities": r.total_liabilities,
            "working_capital": r.working_capital, "retained_earnings": r.retained_earnings,
            "ebit": r.ebit, "revenue": r.revenue, "gross_profit": r.gross_profit,
            "operating_income": r.operating_income, "net_income": r.net_income,
            "operating_cash_flow": r.operating_cash_flow, "shares_outstanding": r.shares_outstanding,
            "eps": r.eps,
        })
    out.sort(key=lambda p: p["fiscal_period_end"], reverse=True)
    return out


def _save_cache(ticker: str, periods: list[dict]) -> None:
    rows = [
        FinancialMetricsCache(
            ticker=ticker,
            fiscal_period_end=p["fiscal_period_end"],
            data_source=p["data_source"],
            disclosure_date=p.get("disclosure_date"),
            total_assets=p.get("total_assets"), current_assets=p.get("current_assets"),
            current_liabilities=p.get("current_liabilities"), total_liabilities=p.get("total_liabilities"),
            working_capital=p.get("working_capital"), retained_earnings=p.get("retained_earnings"),
            ebit=p.get("ebit"), revenue=p.get("revenue"), gross_profit=p.get("gross_profit"),
            operating_income=p.get("operating_income"), net_income=p.get("net_income"),
            operating_cash_flow=p.get("operating_cash_flow"), shares_outstanding=p.get("shares_outstanding"),
            eps=p.get("eps"),
        )
        for p in periods
    ]
    if rows:
        FinancialMetricsCache.objects.using("knowledge_graphdb").bulk_create(rows, ignore_conflicts=True)


# ── 主入口 ───────────────────────────────────────────────────────────────

def get_financial_history(ticker: str, as_of_date: date) -> dict:
    """
    回傳 {"data_source", "quarterly": [...], "annual_revenue": [...]}。
    quarterly 只包含 disclosure_date <= as_of_date 的期別（避免用到 as_of_date
    當下還沒公告的資料），依期末日新到舊排序。data_source 為 None 代表兩邊
    都抓不到可用資料，呼叫端應標記「無法評估」。
    """
    cached = _load_cached(ticker)
    usable = [p for p in cached if p["disclosure_date"] and p["disclosure_date"] <= as_of_date]

    if not usable:
        fresh = _fetch_yfinance_periods(ticker)
        source = "yfinance"
        if not fresh:
            start = as_of_date.replace(year=as_of_date.year - 4)
            fresh = _fetch_finmind_periods(ticker, start, as_of_date)
            source = "finmind" if fresh else None
        if fresh:
            _save_cache(ticker, fresh)
        usable = [p for p in fresh if p["disclosure_date"] and p["disclosure_date"] <= as_of_date]
        cached = fresh

    if not usable:
        return {"data_source": None, "quarterly": [], "annual_revenue": []}

    annual_revenue = []
    if usable[0]["data_source"] == "yfinance":
        annual_revenue = _fetch_yfinance_annual_revenue(ticker)
    if not annual_revenue:
        # FinMind 來源或 yfinance 年報抓不到時，用逐季資料按日曆年加總近似全年營收。
        by_year: dict[int, float] = {}
        for p in cached:
            if p.get("revenue") is None:
                continue
            y = p["fiscal_period_end"].year
            by_year[y] = by_year.get(y, 0) + p["revenue"]
        annual_revenue = [{"year": y, "revenue": v} for y, v in sorted(by_year.items(), reverse=True)]

    return {"data_source": usable[0]["data_source"], "quarterly": usable, "annual_revenue": annual_revenue}
