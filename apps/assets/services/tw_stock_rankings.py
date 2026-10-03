# apps/assets/services/tw_stock_rankings.py
"""
台股個股排名（成交量／成交價／漲跌幅）。

資料來源：TWSE 開放 API STOCK_DAY_ALL（見 twse_market_data.py，個股跟 ETF 共用同一份
in-process 快取），這裡只負責濾出個股（排除 twse_market_data.is_tw_etf 判定為 ETF 的代碼）。
"""
from .ranking_utils import sort_rows, filter_by_query
from .twse_market_data import get_cached_rows

_SORT_KEYS = {
    'volume': 'volume',
    'price': 'close',
    'change': 'change_pct',
}


def get_tw_stock_rankings(sort: str, direction: str, limit: int, offset: int,
                           symbols: list[str] | None = None, q: str | None = None) -> dict:
    all_rows, as_of_date = get_cached_rows()
    rows = [{k: v for k, v in r.items() if k != 'is_etf'} for r in all_rows if not r['is_etf']]
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
