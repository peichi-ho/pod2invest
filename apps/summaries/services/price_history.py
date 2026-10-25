"""
共用的歷史收盤價抓取：一般標的用 yfinance，Yahoo 沒有資料的標的改走其他來源。

目前的特例：
  ^TWOTCI（櫃買指數）——Yahoo 沒有櫃買指數，改抓櫃買中心網站的「櫃買指數月查詢」
  （一次一個月的每日開高低收）。這不是正式公開的 OpenAPI（OpenAPI 的 /tpex_index 只有當月），
  網址或格式之後可能會變，所以抓失敗時回傳空的 Series，呼叫端會當成「抓不到價格」處理，
  不會讓整個流程出錯。
"""
import threading
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests

TPEX_INDEX_TICKER = "^TWOTCI"
_TPEX_INDEX_MONTH_URL = "https://www.tpex.org.tw/web/stock/iNdex_info/inxh/Inx_result.php?l=zh-tw&d={roc_year}/{month:02d}&o=json"

_lock = threading.Lock()
_tpex_month_cache: dict[tuple[int, int], tuple[float, dict[date, float]]] = {}
_CURRENT_MONTH_TTL_SECONDS = 3600  # 當月資料每天會多一筆，快取一小時；過去月份不會變，一直沿用


def _fetch_tpex_index_month(year: int, month: int) -> dict[date, float]:
    """{日期: 收盤}，抓不到回傳空 dict。"""
    url = _TPEX_INDEX_MONTH_URL.format(roc_year=year - 1911, month=month)
    for attempt in range(3):
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            res.raise_for_status()
            tables = res.json().get("tables") or []
            rows = tables[0].get("data") if tables else []
            out = {}
            for row in rows or []:
                # ["2025/06/02", 開市, 最高, 最低, 收市, 漲跌]
                d = datetime.strptime(row[0].strip(), "%Y/%m/%d").date()
                out[d] = float(str(row[4]).replace(",", ""))
            return out
        except Exception:
            if attempt == 2:
                return {}
            time.sleep(2)
    return {}


def _tpex_index_month(year: int, month: int) -> dict[date, float]:
    today = date.today()
    is_current = (year, month) == (today.year, today.month)
    key = (year, month)
    with _lock:
        hit = _tpex_month_cache.get(key)
        if hit and (not is_current or time.time() - hit[0] < _CURRENT_MONTH_TTL_SECONDS):
            return hit[1]
    data = _fetch_tpex_index_month(year, month)
    if data or not is_current:
        with _lock:
            _tpex_month_cache[key] = (time.time(), data)
    return data


def _tpex_index_closes(start: date, end: date) -> pd.Series:
    if start > date.today():
        return pd.Series(dtype=float)
    closes: dict[date, float] = {}
    y, m = start.year, start.month
    last = min(end, date.today())
    while (y, m) <= (last.year, last.month):
        closes.update(_tpex_index_month(y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    picked = {d: c for d, c in closes.items() if start <= d < end}
    if not picked:
        return pd.Series(dtype=float)
    s = pd.Series(picked).sort_index()
    s.index = pd.to_datetime(s.index)
    return s


def get_close_history(ticker: str, start: date, end: date) -> pd.Series:
    """
    start（含）到 end（不含）之間每個交易日的收盤價，index 是日期，跟 yfinance 的 history()["Close"] 同樣用法。
    抓不到回傳空的 Series。
    """
    if isinstance(start, datetime):
        start = start.date()
    if isinstance(end, datetime):
        end = end.date()
    if ticker == TPEX_INDEX_TICKER:
        return _tpex_index_closes(start, end)
    import yfinance as yf
    hist = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat(), auto_adjust=True)
    if hist.empty:
        return pd.Series(dtype=float)
    return hist["Close"]
