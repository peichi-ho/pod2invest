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

from .industry_zh import translate_industry
from .etf_classification import classify_etf
from .industry_benchmarks import get_industry_benchmarks

DB = "etfdb"


def get_tw_stock_basic_info(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(f"{symbol}.TW")
        info = ticker.info
    except Exception:
        return None
    if not info:
        return None

    # 產業分類、同業本益比／負債比基準都改用 TWSE 官方資料（見 industry_benchmarks.py）；
    # 查不到（例如還沒被 TWSE 基本資料收錄的新股）才退回 yfinance 的 sector/industry 翻譯。
    industry = get_industry_benchmarks(symbol)
    debt_ratio = industry['own_debt_ratio'] if industry else None
    if debt_ratio is None:
        debt_ratio = _get_debt_ratio(ticker)

    return {
        'symbol': symbol,
        'name': info.get('longName') or info.get('shortName') or symbol,
        'market_cap': info.get('marketCap'),
        'market_cap_change_1y': info.get('52WeekChange'),
        'pe_ratio': info.get('trailingPE'),
        'pe_industry_benchmark': industry['pe_benchmark'] if industry else None,
        'eps': info.get('trailingEps'),
        'eps_yoy_change': info.get('earningsQuarterlyGrowth') if info.get('earningsQuarterlyGrowth') is not None else info.get('earningsGrowth'),
        'revenue_growth': info.get('revenueGrowth'),
        'debt_ratio': debt_ratio,
        'debt_ratio_industry_benchmark': industry['debt_ratio_benchmark'] if industry else None,
        'is_finance_industry': industry['is_finance'] if industry else False,
        'dividend_yield': info.get('dividendYield'),
        'week52_high': info.get('fiftyTwoWeekHigh'),
        'week52_low': info.get('fiftyTwoWeekLow'),
        'sector': info.get('sector'),
        'industry': industry['industry_name'] if industry else translate_industry(info.get('industry'), info.get('sector')),
    }


def _get_debt_ratio(ticker: yf.Ticker) -> float | None:
    """負債比（%）＝總負債／總資產，查資產負債表算，比 yfinance .info 的
    debtToEquity（負債權益比，定義不同）更貼近台灣慣用的負債比。"""
    try:
        bs = ticker.balance_sheet
        liabilities = bs.loc['Total Liabilities Net Minority Interest'].iloc[0]
        assets = bs.loc['Total Assets'].iloc[0]
        if not assets:
            return None
        return float(liabilities) / float(assets) * 100
    except Exception:
        return None


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
    classification = classify_etf(r['symbol']) or {}
    return {
        'symbol': r['symbol'],
        'name': r['name'] or '',
        'strategy_type': classification.get('strategy_type'),
        'theme': classification.get('theme'),
        'tracking_index_name': r['tracking_index_name'],
        'distribution_policy': r['distribution_policy'],
        'inception_date': r['inception_date'].isoformat() if r['inception_date'] else None,
        'aum': float(r['aum']) if r['aum'] is not None else None,
        'aum_as_of_date': r['aum_as_of_date'].isoformat() if r['aum_as_of_date'] else None,
        'mgmt_fee': float(r['mgmt_fee']) if r['mgmt_fee'] is not None else None,
        'custody_fee': float(r['custody_fee']) if r['custody_fee'] is not None else None,
        'ter': float(r['ter']) if r['ter'] is not None else None,
    }
