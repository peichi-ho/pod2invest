# apps/assets/services/tw_stock_rankings.py
"""
台股個股排名（成交量／成交價／漲跌幅）。

資料來源：TWSE 開放 API STOCK_DAY_ALL，一次回傳全市場（個股跟 ETF 混在同一份清單裡），
本檔負責濾掉 ETF 代碼（規則跟 apps/calculator/views.py 的 _is_tw_etf 一致），只留下個股。
in-process 快取一段時間，避免每個使用者的請求都重打一次 TWSE（這是全市場共用的一次查詢，
不是每人各自查，所以用模組層級快取即可，不用存資料庫）。
"""
import re
from datetime import date, datetime, timedelta

import requests

from .ranking_utils import sort_rows, filter_by_query

_STOCK_DAY_ALL_URL = 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'
_CACHE_TTL_SECONDS = 300  # 5 分鐘

_cache_rows = None
_cache_as_of_date = None
_cache_fetched_at = None


def _is_tw_etf(code: str) -> bool:
    """
    台灣 ETF 代碼判斷：00 開頭，4–6 位數字，如 0050、0056、006208、00878，
    後面可能再接一個英文字母（主動式 ETF，如 00403A、00981A）。

    注意：這條規則比 apps/calculator/views.py 的同名函式多涵蓋了字母後綴這個新格式——
    實測 TWSE STOCK_DAY_ALL 資料時發現舊規則會把 00403A/00981A 這類主動式 ETF
    誤判成個股（名稱其實是「主動統一升級50」「主動統一台股增長」），混進台股排名榜。
    """
    return bool(re.match(r'^00\d{2,4}[A-Z]?$', code))


def _to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _to_int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _parse_roc_date(roc_str: str):
    """TWSE 回傳的 Date 是民國年，如 '1150814' -> 2026-08-14。解析失敗回傳 None。"""
    try:
        roc_str = str(roc_str)
        year = int(roc_str[:-4]) + 1911
        month = int(roc_str[-4:-2])
        day = int(roc_str[-2:])
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


def _fetch_and_parse():
    res = requests.get(_STOCK_DAY_ALL_URL, timeout=15, headers={'Accept': 'application/json'})
    res.raise_for_status()
    raw = res.json()

    as_of_date = None
    rows = []
    for item in raw:
        code = (item.get('Code') or '').strip()
        if not code or _is_tw_etf(code):
            continue

        close = _to_float(item.get('ClosingPrice'))
        volume = _to_int(item.get('TradeVolume'))
        if close is None or volume is None:
            continue

        change_abs = _to_float(item.get('Change'))
        prev_close = (close - change_abs) if change_abs is not None else None
        change_pct = (change_abs / prev_close * 100) if (change_abs is not None and prev_close) else None

        if as_of_date is None:
            as_of_date = _parse_roc_date(item.get('Date'))

        rows.append({
            'symbol': code,
            'name': item.get('Name', ''),
            'market': 'TWSE',
            'close': close,
            'volume': volume,
            'change_abs': change_abs,
            'change_pct': change_pct,
        })
    return rows, as_of_date


def _get_cached_rows():
    global _cache_rows, _cache_as_of_date, _cache_fetched_at
    now = datetime.now()
    if (_cache_rows is not None and _cache_fetched_at
            and (now - _cache_fetched_at) < timedelta(seconds=_CACHE_TTL_SECONDS)):
        return _cache_rows, _cache_as_of_date

    rows, as_of_date = _fetch_and_parse()
    _cache_rows = rows
    _cache_as_of_date = as_of_date
    _cache_fetched_at = now
    return rows, as_of_date


_SORT_KEYS = {
    'volume': 'volume',
    'price': 'close',
    'change': 'change_pct',
}


def get_tw_stock_rankings(sort: str, direction: str, limit: int, offset: int,
                           symbols: list[str] | None = None, q: str | None = None) -> dict:
    rows, as_of_date = _get_cached_rows()
    if symbols:
        wanted = set(symbols)
        rows = [r for r in rows if r['symbol'] in wanted]
    rows = filter_by_query(rows, q)
    key = _SORT_KEYS.get(sort, 'volume')
    page, total = sort_rows(rows, key, direction, limit, offset)

    return {
        'as_of_date': as_of_date.isoformat() if as_of_date else None,
        'count': total,
        'data': page,
    }
