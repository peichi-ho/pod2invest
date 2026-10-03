# apps/assets/services/tw_etf_rankings.py
"""
台股 ETF 排名（成交量／成交價／漲跌幅）。

資料來源：TWSE 開放 API STOCK_DAY_ALL（見 twse_market_data.py，個股跟 ETF 共用同一份
in-process 快取），這裡只負責濾出 ETF（twse_market_data.is_tw_etf 判定為 ETF 的代碼）。

原本是查 etfdb（獨立 Supabase 專案）的 etf_prices + etf_master，但那個資料庫常常連不上
（見 apps/assets/services/basic_info.py 的 get_tw_etf_basic_info，詳情頁的基本資料
——追蹤指數/配息政策/規模/費用率等——目前還是得靠它，TWSE 開放 API 沒有這些）。
排名榜只需要價量，改用跟個股同一支 TWSE API，不再受 etfdb 連線狀況影響。
"""
from .ranking_utils import sort_rows, filter_by_query
from .twse_market_data import get_cached_rows

_SORT_KEYS = {
    'volume': 'volume',
    'price': 'close',
    'change': 'change_pct',
}


def get_tw_etf_rankings(sort: str, direction: str, limit: int, offset: int,
                         symbols: list[str] | None = None, q: str | None = None) -> dict:
    all_rows, as_of_date = get_cached_rows()
    rows = [{k: v for k, v in r.items() if k != 'is_etf'} for r in all_rows if r['is_etf']]
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
