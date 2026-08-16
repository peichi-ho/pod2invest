# apps/assets/services/tw_etf_rankings.py
"""
台股 ETF 排名（成交量／成交價／漲跌幅）。

資料來源：etfdb（獨立 Supabase 專案）的 etf_prices（apps/etf/management/commands/
sync_etf_prices_daily.py 每日排程寫入，按月 partition）+ etf_master（ETF 基本清單）。

漲跌幅算法：抓「最新交易日」與「前一交易日」兩個明確日期各自的收盤價做 LEFT JOIN，
不用 LAG() OVER (PARTITION BY symbol ORDER BY trade_date) —— etf_prices 是按月分區表，
LAG() 得掃過每支 ETF 的完整歷史才能算出一列，會打散 partition pruning；固定兩個已知日期
查詢可以精準命中對應月份的分區，速度快很多。新上市、還沒有前一日資料的 ETF，
prev_close 會是 NULL，change_pct 一律回 None，排序時排到最後（sort_rows 已處理）。
"""
from django.db import connections

from .ranking_utils import sort_rows, filter_by_query

DB = "etfdb"

_RANKING_SQL = """
WITH dates AS (
    SELECT MAX(trade_date) AS latest_date FROM etf_prices
),
prev AS (
    SELECT MAX(trade_date) AS prev_date
    FROM etf_prices, dates
    WHERE trade_date < dates.latest_date
)
SELECT
    cur.symbol, cur.exchange, m.name, cur.close, cur.volume, cur.trade_date,
    p.close AS prev_close
FROM etf_prices cur
JOIN etf_master m
    ON m.symbol = cur.symbol AND m.exchange = cur.exchange AND m.status = 'active'
CROSS JOIN dates
LEFT JOIN etf_prices p
    ON p.symbol = cur.symbol AND p.exchange = cur.exchange
   AND p.trade_date = (SELECT prev_date FROM prev)
WHERE cur.trade_date = dates.latest_date;
"""

_SORT_KEYS = {
    'volume': 'volume',
    'price': 'close',
    'change': 'change_pct',
}


def _fetch_rows():
    with connections[DB].cursor() as cur:
        cur.execute(_RANKING_SQL)
        columns = [c[0] for c in cur.description]
        raw_rows = cur.fetchall()

    as_of_date = None
    rows = []
    for values in raw_rows:
        r = dict(zip(columns, values))
        close = float(r['close']) if r['close'] is not None else None
        if close is None:
            continue
        volume = int(r['volume']) if r['volume'] is not None else None
        prev_close = float(r['prev_close']) if r['prev_close'] is not None else None
        change_abs = (close - prev_close) if prev_close is not None else None
        change_pct = (change_abs / prev_close * 100) if prev_close else None

        if as_of_date is None:
            as_of_date = r['trade_date']

        rows.append({
            'symbol': r['symbol'],
            'name': r['name'] or '',
            'market': r['exchange'],
            'close': close,
            'volume': volume,
            'change_abs': change_abs,
            'change_pct': change_pct,
        })
    return rows, as_of_date


def get_tw_etf_rankings(sort: str, direction: str, limit: int, offset: int,
                         symbols: list[str] | None = None, q: str | None = None) -> dict:
    rows, as_of_date = _fetch_rows()
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
