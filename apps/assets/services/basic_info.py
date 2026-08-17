# apps/assets/services/basic_info.py
"""
標的詳情頁的「基本資料」。

台股個股：即時查 yfinance .info，欄位不齊全時允許缺漏（yfinance 的 .info 眾所皆知不穩定，
apps/calculator/views.py 的 _get_display_name() 也是同樣的容錯做法）。
台股 ETF：資料已經有現成的每日排程同步進 etfdb（etf_master/etf_aum_daily/etf_fees），
不用再另外爬，直接查資料庫即可。
"""
import yfinance as yf
from django.db import connections

DB = "etfdb"


def get_tw_stock_basic_info(symbol: str) -> dict | None:
    try:
        info = yf.Ticker(f"{symbol}.TW").info
    except Exception:
        return None
    if not info:
        return None
    return {
        'symbol': symbol,
        'name': info.get('longName') or info.get('shortName') or symbol,
        'market_cap': info.get('marketCap'),
        'pe_ratio': info.get('trailingPE'),
        'dividend_yield': info.get('dividendYield'),
        'week52_high': info.get('fiftyTwoWeekHigh'),
        'week52_low': info.get('fiftyTwoWeekLow'),
        'sector': info.get('sector'),
        'industry': info.get('industry'),
    }


_ETF_INFO_SQL = """
SELECT
    m.symbol, m.exchange, m.name, m.tracking_index_name, m.distribution_policy,
    m.inception_date,
    a.aum, a.as_of_date AS aum_as_of_date,
    f.mgmt_fee, f.custody_fee, f.ter
FROM etf_master m
LEFT JOIN LATERAL (
    SELECT aum, as_of_date FROM etf_aum_daily
    WHERE symbol = m.symbol AND exchange = m.exchange
    ORDER BY as_of_date DESC LIMIT 1
) a ON true
LEFT JOIN etf_fees f ON f.symbol = m.symbol AND f.exchange = m.exchange
WHERE m.symbol = %s AND m.status = 'active'
LIMIT 1;
"""


def get_tw_etf_basic_info(symbol: str) -> dict | None:
    with connections[DB].cursor() as cur:
        cur.execute(_ETF_INFO_SQL, (symbol,))
        row = cur.fetchone()
        if not row:
            return None
        columns = [c[0] for c in cur.description]

    r = dict(zip(columns, row))
    return {
        'symbol': r['symbol'],
        'name': r['name'] or '',
        'tracking_index_name': r['tracking_index_name'],
        'distribution_policy': r['distribution_policy'],
        'inception_date': r['inception_date'].isoformat() if r['inception_date'] else None,
        'aum': float(r['aum']) if r['aum'] is not None else None,
        'aum_as_of_date': r['aum_as_of_date'].isoformat() if r['aum_as_of_date'] else None,
        'mgmt_fee': float(r['mgmt_fee']) if r['mgmt_fee'] is not None else None,
        'custody_fee': float(r['custody_fee']) if r['custody_fee'] is not None else None,
        'ter': float(r['ter']) if r['ter'] is not None else None,
    }
