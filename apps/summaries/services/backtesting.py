# apps/summaries/services/backtesting.py
"""
回測相關工具：
  - resolve_ticker()       股票名稱 → Yahoo Finance ticker
  - calc_end_time()        timeframe_raw → 截止日期
  - create_backtesting_rows()  outlook_calls → 寫入 BacktestingRecord
"""
import re
from datetime import date, timedelta
from typing import Optional

from dateutil.relativedelta import relativedelta


# ── Timeframe → 截止日 ────────────────────────────────────────────────────────

def calc_end_time(timeframe_raw: str, start: date) -> Optional[date]:
    """
    把 LLM 產生的 timeframe_raw 換算成截止日。
    具體年份/日期允許最多 3 年；相對描述最多 1 年；超過上限回傳 None。
    """
    import calendar as _cal
    tf = (timeframe_raw or "").strip()
    tf_l = tf.lower()

    start_year = start.year
    next_year = start_year + 1
    abs_max = start + relativedelta(years=3)   # 具體日期上限
    rel_max = start + relativedelta(years=1)   # 相對描述上限

    def _clamp(d: date, limit: date = abs_max) -> Optional[date]:
        return d if start < d <= limit else None

    def _month_end(year: int, month: int) -> date:
        return date(year, month, _cal.monthrange(year, month)[1])

    # ── 1. 具體年份：2025 / 2026年 ───────────────────────────────────────────
    m = re.match(r"^(20\d{2})年?$", tf)
    if m:
        return _clamp(date(int(m.group(1)), 12, 31))

    # 2027年5月 / 2027年5月底
    m = re.match(r"(20\d{2})年(\d{1,2})月", tf)
    if m:
        return _clamp(_month_end(int(m.group(1)), int(m.group(2))))

    # 年份範圍 2030-2035年 → 取前者
    m = re.match(r"(20\d{2})[－\-~～](20\d{2})", tf)
    if m:
        return _clamp(date(int(m.group(1)), 12, 31))

    # ── 2. 明年系列 ──────────────────────────────────────────────────────────
    if "明年" in tf:
        if "上半年" in tf or "前半年" in tf:
            return _clamp(date(next_year, 6, 30))
        if "下半年" in tf or "後半年" in tf:
            return _clamp(date(next_year, 12, 31))
        for q, mo in [("Q1", 3), ("Q2", 6), ("Q3", 9), ("Q4", 12),
                      ("第一季", 3), ("第二季", 6), ("第三季", 9), ("第四季", 12)]:
            if q in tf:
                return _clamp(_month_end(next_year, mo))
        m = re.search(r"(\d{1,2})月", tf)
        if m:
            return _clamp(_month_end(next_year, int(m.group(1))))
        return _clamp(date(next_year, 12, 31))   # 明年（一般）→ 明年底

    # ── 3. 今年 / 年底 系列 ──────────────────────────────────────────────────
    if any(k in tf for k in ("年底", "今年底", "年底前")):
        return _clamp(date(start_year, 12, 31))
    if any(k in tf for k in ("今年年中", "年中")):
        return _clamp(date(start_year, 6, 30))
    if "今年" in tf:
        return _clamp(date(start_year, 12, 31))

    # ── 4. 季度（不含明年）────────────────────────────────────────────────────
    for q_key, mo in [("Q4", 12), ("第四季", 12),
                      ("Q3", 9),  ("第三季", 9),
                      ("Q2", 6),  ("第二季", 6),
                      ("Q1", 3),  ("第一季", 3)]:
        if q_key in tf:
            candidate = _month_end(start_year, mo)
            if candidate <= start:          # 今年已過，推到明年
                candidate = _month_end(next_year, mo)
            return _clamp(candidate)

    # ── 5. 上 / 下半年 ───────────────────────────────────────────────────────
    if "上半年" in tf:
        c = date(start_year, 6, 30)
        return _clamp(c if c > start else date(next_year, 6, 30))
    if "下半年" in tf:
        c = date(start_year, 12, 31)
        return _clamp(c if c > start else date(next_year, 12, 31))

    # ── 6. 具體月份（11月、11月到4月、11、12月）────────────────────────────
    # 跨年範圍：11月到4月 → 取終點
    m = re.match(r"(\d{1,2})[月、，,]\s*(\d{1,2})月?\s*(?:到|至|-|～)\s*(\d{1,2})月", tf)
    if not m:
        m = re.match(r"(\d{1,2})月?\s*(?:到|至|[-～])\s*(\d{1,2})月", tf)
        if m:
            end_mo = int(m.group(2))
            c = _month_end(start_year, end_mo)
            if c <= start:
                c = _month_end(next_year, end_mo)
            return _clamp(c)

    m = re.match(r"^(\d{1,2})月$", tf)
    if m:
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            c = _month_end(start_year, mo)
            if c <= start:
                c = _month_end(next_year, mo)
            return _clamp(c)

    # ── 7. 相對描述（上限 rel_max = 1 年）────────────────────────────────────
    def _rel(delta) -> Optional[date]:
        return _clamp(start + delta, limit=rel_max)

    # 多月份列舉：11、12月 → 取最後一個月
    m = re.search(r"(\d{1,2})[月、，,]+(\d{1,2})月", tf)
    if m:
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            c = _month_end(start_year, mo)
            if c <= start:
                c = _month_end(next_year, mo)
            return _clamp(c)

    # 多年（超過可評估範圍，skip）
    if any(k in tf_l for k in ("未來幾年", "未來兩三年", "未來3-5年", "未來五年",
                                "十幾年", "幾年")):
        return None
    if any(k in tf_l for k in ("未來兩年", "未來2年", "兩年", "2年")):
        return _clamp(start + relativedelta(years=2))
    if any(k in tf_l for k in ("未來5季", "未來五季")):
        return _clamp(start + relativedelta(months=15))
    if any(k in tf_l for k in ("未來四季", "下個財年度", "未來一年", "未來1年", "一年")):
        return _rel(relativedelta(years=1))
    # 長期
    if any(k in tf_l for k in ("長期", "長線", "中長線", "long-term", "long", "每年")):
        return _rel(relativedelta(years=1))
    # 中期
    if any(k in tf_l for k in ("數個月到半年", "短期至中期", "短中期", "中短期")):
        return _rel(relativedelta(months=4))
    if any(k in tf_l for k in ("中期", "半年", "未來")):
        return _rel(relativedelta(months=6))
    # 短期
    if any(k in tf_l for k in ("短期", "短線", "一陣子")):
        return _rel(relativedelta(months=3))

    return None  # 無法解析


# ── 股票名稱 → ticker ─────────────────────────────────────────────────────────

def _rule_based_ticker(asset: str) -> Optional[str]:
    """第一層：規則直判，不查資料庫。"""
    a = asset.strip()

    # 純數字台股代號
    if re.match(r"^\d{4,6}$", a):
        return f"{a}.TW"

    # 已是英文 ticker（1-5 大寫字母）
    if re.match(r"^[A-Z]{1,5}$", a):
        return a

    # 指數關鍵字
    INDEX_MAP = {
        ("台股加權", "加權指數", "大盤", "台股"): "^TWII",
        ("那斯達克", "Nasdaq", "nasdaq"):         "^IXIC",
        ("S&P500", "S&P 500", "標普"):            "^GSPC",
        ("費半",):                                 "^SOX",
        ("道瓊",):                                 "^DJI",
    }
    for keys, ticker in INDEX_MAP.items():
        if any(k in a for k in keys):
            return ticker

    # 大宗商品
    COMMODITY_MAP = {
        ("黃金",): "GC=F",
        ("原油",): "CL=F",
    }
    for keys, ticker in COMMODITY_MAP.items():
        if any(k in a for k in keys):
            return ticker

    return None


def resolve_ticker(asset: str) -> str:
    """
    股票名稱 → Yahoo Finance ticker。
    查不到回傳空字串（後續標記 skip）。
    """
    # 第一層：規則
    t = _rule_based_ticker(asset)
    if t:
        return t

    # 第二層：TickerMap 資料庫
    try:
        from apps.summaries.models import TickerMap
        row = TickerMap.objects.using("summariesdb").filter(asset_name=asset).first()
        if row:
            return row.ticker
    except Exception:
        pass

    return ""  # 無法解析


# ── 建立回測列 ────────────────────────────────────────────────────────────────

def create_backtesting_rows(
    *,
    summary_record,
    episode_id: Optional[int],
    outlook_calls: list[dict],
    published_at,          # datetime 或 date，可為 None
):
    """
    每筆 outlook_call（有 timeframe）建一列 BacktestingRecord。
    published_at=None 或 end_time 算不出來 → result=skip。
    """
    from apps.summaries.models import BacktestingRecord

    if not outlook_calls:
        return

    # published_at 轉成 date
    if published_at is None:
        start = None
    elif hasattr(published_at, "date"):
        start = published_at.date()
    else:
        start = published_at  # 已是 date

    rows = []
    for call in outlook_calls:
        tf_raw = (call.get("timeframe") or "").strip()
        if not tf_raw or tf_raw.lower() in ("null", "none"):
            continue  # 無 timeframe，不做回測

        ticker = resolve_ticker(call.get("asset", ""))

        # 計算截止日
        if start:
            end_time = calc_end_time(tf_raw, start)
        else:
            end_time = None

        # 無法解析 ticker 或 end_time → skip
        result = BacktestingRecord.RESULT_PENDING
        if not ticker or not end_time:
            result = BacktestingRecord.RESULT_SKIP

        rows.append(BacktestingRecord(
            summary=summary_record,
            episode_id=episode_id,
            asset=call.get("asset", ""),
            ticker=ticker,
            direction=call.get("direction", ""),
            timeframe_raw=tf_raw,
            thesis=call.get("thesis", ""),
            evidence_quote=call.get("evidence_quote", ""),
            target_price=None,          # 目前 LLM 不輸出明確目標價
            start_time=start,
            end_time=end_time,
            result=result,
        ))

    if rows:
        BacktestingRecord.objects.using("summariesdb").bulk_create(rows)
