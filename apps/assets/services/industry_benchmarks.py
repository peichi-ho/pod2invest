# apps/assets/services/industry_benchmarks.py
"""
同產業平均本益比／負債比，給個股詳情頁「高於/低於/接近產業平均」的提示用。

三個 TWSE 開放 API 各打一次、全市場一次到位，取代逐檔查 yfinance（太慢也太不穩定，
1000多檔股票不可能每次詳情頁請求都即時查一輪）：
- t187ap03_L：每檔股票的官方產業別代碼（上市公司基本資料）
- BWIBBU_ALL：每檔股票的本益比
- t187ap07_L_ci：每檔股票的資產負債表（只涵蓋「一般業」——金融保險業的財報科目完全不同
  ［存款/放款而非流動資產/流動負債］，TWSE 本來就是分開用不同資料集發布，也不適合跟一般
  產業比負債比，所以這份資料本來就沒有金融股）

同產業樣本數 <10 用平均值、>=10 用中位數：樣本數夠大時中位數比較不怕被單一極端值拉歪，
樣本數太小（例如玻璃陶瓷業只有5家）時中位數只取決於一兩檔公司，平均值反而比較能反映整體。
"""
import statistics
from datetime import datetime, timedelta

import requests

_COMPANY_INFO_URL = 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L'
_PE_URL = 'https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL'
_BALANCE_SHEET_URL = 'https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci'
_CACHE_TTL_SECONDS = 6 * 3600  # 本益比每天變、財報要等季報才變，不用像即時股價那樣頻繁重抓

FINANCE_INDUSTRY_CODE = '17'

# TWSE 官方產業別代碼 → 中文名稱，對照 t187ap03_L 的「產業別」欄位（已用代表性公司驗證過，
# 例如 24=台積電/聯電/華邦電、17=彰銀/台中銀/旺旺保等金控銀行、31=鴻海）。
INDUSTRY_NAMES: dict[str, str] = {
    '01': '水泥工業', '02': '食品工業', '03': '塑膠工業', '04': '紡織纖維',
    '05': '電機機械', '06': '電器電纜', '08': '玻璃陶瓷', '09': '造紙工業',
    '10': '鋼鐵工業', '11': '橡膠工業', '12': '汽車工業', '14': '建材營造',
    '15': '航運業', '16': '觀光餐旅', '17': '金融保險業', '18': '貿易百貨',
    '20': '其他', '21': '化學工業', '22': '生技醫療業', '23': '油電燃氣業',
    '24': '半導體業', '25': '電腦及週邊設備業', '26': '光電業', '27': '通信網路業',
    '28': '電子零組件業', '29': '電子通路業', '30': '資訊服務業', '31': '其他電子業',
    '35': '綠能環保', '36': '數位雲端', '37': '運動休閒', '38': '居家生活',
    '91': '存託憑證',
}

_cache = None
_cache_fetched_at = None


def _to_float(s):
    try:
        v = float(s)
        return v if v == v else None  # 過濾 NaN
    except (TypeError, ValueError):
        return None


def _fetch_industry_by_symbol() -> dict[str, str]:
    res = requests.get(_COMPANY_INFO_URL, timeout=15)
    res.raise_for_status()
    out = {}
    for row in res.json():
        symbol = (row.get('公司代號') or '').strip()
        code = (row.get('產業別') or '').strip()
        if symbol and code:
            out[symbol] = code
    return out


def _fetch_pe_by_symbol() -> dict[str, float]:
    res = requests.get(_PE_URL, timeout=15)
    res.raise_for_status()
    out = {}
    for row in res.json():
        symbol = (row.get('Code') or '').strip()
        pe = _to_float(row.get('PEratio'))
        if symbol and pe:
            out[symbol] = pe
    return out


def _fetch_debt_ratio_by_symbol() -> dict[str, float]:
    res = requests.get(_BALANCE_SHEET_URL, timeout=15)
    res.raise_for_status()
    out = {}
    for row in res.json():
        symbol = (row.get('公司代號') or '').strip()
        assets = _to_float(row.get('資產總計'))
        liabilities = _to_float(row.get('負債總計'))
        if symbol and assets:
            out[symbol] = liabilities / assets * 100
    return out


def _benchmark(values: list[float]) -> dict | None:
    n = len(values)
    if n == 0:
        return None
    if n < 10:
        return {'value': statistics.mean(values), 'method': 'mean', 'n': n}
    return {'value': statistics.median(values), 'method': 'median', 'n': n}


def _build_cache() -> dict:
    industry_by_symbol = _fetch_industry_by_symbol()
    pe_by_symbol = _fetch_pe_by_symbol()
    debt_ratio_by_symbol = _fetch_debt_ratio_by_symbol()

    pe_by_industry: dict[str, list[float]] = {}
    debt_ratio_by_industry: dict[str, list[float]] = {}
    for symbol, code in industry_by_symbol.items():
        pe = pe_by_symbol.get(symbol)
        if pe:
            pe_by_industry.setdefault(code, []).append(pe)
        debt_ratio = debt_ratio_by_symbol.get(symbol)
        if debt_ratio is not None:
            debt_ratio_by_industry.setdefault(code, []).append(debt_ratio)

    return {
        'industry_by_symbol': industry_by_symbol,
        'debt_ratio_by_symbol': debt_ratio_by_symbol,
        'pe_benchmark_by_industry': {c: _benchmark(v) for c, v in pe_by_industry.items()},
        'debt_ratio_benchmark_by_industry': {c: _benchmark(v) for c, v in debt_ratio_by_industry.items()},
    }


def _get_cache() -> dict:
    global _cache, _cache_fetched_at
    now = datetime.now()
    if (_cache is not None and _cache_fetched_at
            and (now - _cache_fetched_at) < timedelta(seconds=_CACHE_TTL_SECONDS)):
        return _cache
    try:
        _cache = _build_cache()
        _cache_fetched_at = now
    except Exception:
        # 抓不到就沿用舊快取（就算過期也比完全沒有好），真的沒有舊快取才讓呼叫端拿到 None。
        if _cache is None:
            raise
    return _cache


def get_industry_benchmarks(symbol: str) -> dict | None:
    """
    回傳這檔股票的 TWSE 官方產業分類跟同業本益比／負債比基準。查不到（例如還沒被
    TWSE 基本資料收錄的新股）就回傳 None，呼叫端應該退回原本沒有同業比較的呈現方式。
    """
    try:
        cache = _get_cache()
    except Exception:
        return None
    code = cache['industry_by_symbol'].get(symbol)
    if not code:
        return None
    return {
        'industry_code': code,
        'industry_name': INDUSTRY_NAMES.get(code, code),
        'is_finance': code == FINANCE_INDUSTRY_CODE,
        'own_debt_ratio': cache['debt_ratio_by_symbol'].get(symbol),
        'pe_benchmark': cache['pe_benchmark_by_industry'].get(code),
        'debt_ratio_benchmark': cache['debt_ratio_benchmark_by_industry'].get(code),
    }
