# apps/assets/services/basic_info.py
"""
標的詳情頁的「基本資料」。

台股個股：即時查 yfinance .info，欄位不齊全時允許缺漏（yfinance 的 .info 眾所皆知不穩定，
apps/calculator/views.py 的 _get_display_name() 也是同樣的容錯做法）。
台股 ETF：原本查 etfdb（Supabase）的 etf_master/etf_aum_daily/etf_fees，但那個資料庫已經
掛掉，改成跟個股一樣即時查 yfinance + TWSE 開放 API（見 etf_twse_meta.py）。配息政策
（月配/季配/年配）跟費用率（TER/經理費/保管費）這兩項，yfinance 的 ETF .info 跟 TWSE
開放資料目前都查不到對應資料集，先固定回傳 None，前端顯示「—」。
"""
import yfinance as yf

from .industry_zh import translate_industry
from .etf_classification import classify_etf
from .industry_benchmarks import get_industry_benchmarks
from .etf_twse_meta import get_etf_twse_meta


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


def get_tw_etf_basic_info(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(f"{symbol}.TW")
        info = ticker.info
    except Exception:
        info = None
    if not info or info.get('quoteType') != 'ETF':
        return None

    meta = get_etf_twse_meta(symbol) or {}
    classification = classify_etf(symbol) or {}

    strategy_type = classification.get('strategy_type')
    if not strategy_type and meta.get('fund_type_label'):
        # 沒有手動維護的分類（見 etf_classification.py）時，退回 TWSE 官方基金類型
        # 翻成的短標籤（例如「國內指數型」），跟 etf_twse_meta.py 的 FUND_TYPE_LABELS
        # 保持同一套短標籤風格，不要整格塞一句法規長句。
        strategy_type = meta['fund_type_label']

    aum = info.get('totalAssets') or info.get('netAssets')

    # 前端目前只顯示分類／追蹤指數／規模三格（見 static/js/assets.js
    # _renderAssetBasicInfoTiles）。近一年報酬／配息／費用率／成立日先拿掉，
    # 之後要重新顯示時，近一年報酬可以用 ticker.history(period='1y') 首尾收盤價算
    # （公式跟 calculator.js renderStockChart() 算「區間漲跌」一樣），成立日可以用
    # meta['inception_date']；配息政策／費用率兩邊資料源目前都查不到。
    #
    # 主動式基金沒有追蹤指數是「事實」，不是「查不到」，這裡只回傳 is_active_fund
    # 這個布林值，「主動式基金（無追蹤指數）」這句文案跟怎麼分兩行顯示交給前端的
    # _etfTrackingIndexValue() 決定，後端不組字串。
    return {
        'symbol': symbol,
        'name': info.get('longName') or info.get('shortName') or symbol,
        'strategy_type': strategy_type,
        'theme': classification.get('theme'),
        'tracking_index_name': meta.get('tracking_index_name'),
        'is_active_fund': bool(meta.get('is_active')),
        'aum': float(aum) if aum is not None else None,
    }
