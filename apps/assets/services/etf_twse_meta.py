# apps/assets/services/etf_twse_meta.py
"""
台股 ETF 詳情頁的「投資類型／追蹤指數／成立日」。

etfdb（Supabase）已經掛掉，原本查 etf_master 的做法整個不能用。改用 TWSE 開放 API
的「基金基本資料彙總表」（t187ap47_L）——涵蓋所有上市 ETF（含主動式），全市場一次
抓回來（跟 twse_market_data.py／industry_benchmarks.py 同樣的做法），不用逐檔查。

這份資料集沒有配息政策（月配/季配/年配）跟費用率（TER/經理費/保管費），TWSE
開放資料裡目前找不到對應資料集，yfinance 的 ETF .info 也沒有這兩項，所以
get_tw_etf_basic_info()（見 basic_info.py）這兩個欄位會是 None，前端顯示「—」。
"""
from datetime import datetime, timedelta

import requests

from .etf_classification import STRATEGY_TYPE
from .twse_market_data import parse_roc_date

_FUND_INFO_URL = 'https://openapi.twse.com.tw/v1/opendata/t187ap47_L'
_CACHE_TTL_SECONDS = 6 * 3600  # 基金基本資料幾乎不會變，跟 industry_benchmarks.py 同樣的快取時間

# TWSE「基金類型」欄位是官方法規分類（境內/境外成分＋一般/主動式＋資產類別拼出來的
# 長句子），2026-09 實測全市場 271 檔基金只會落在這 11 種固定字串裡——不是自由文字，
# 用查表翻成短標籤最穩，不必寫正則去拆句子。翻譯後長度跟語氣對齊
# etf_classification.py 的 STRATEGY_TYPE（市值型/高股息型…），槓桿反向、商品型兩個
# 概念直接共用同一份 STRATEGY_TYPE 常數，跟手動維護那份分類表保持同一套用字。
#
# 注意：TWSE 這份資料只描述「法規結構」（境內/境外、主動/被動、資產類別），沒有
# 「市值型 vs 高股息型」這種投資策略的維度——那是 etf_classification.py 手動判斷
# 才有的資訊，這裡的翻譯無法、也不應該假裝有。找不到對照（TWSE 之後新增分類）就
# 照原樣顯示那句官方長字串，不會噴錯，只是不會像其他標的那麼精簡。
FUND_TYPE_LABELS: dict[str, str] = {
    '國內成分證券指數股票型基金': '國內指數型',
    '國外成分證券指數股票型基金': '海外指數型',
    '國外成份/加掛外幣證券指數股票型基金': '海外指數型（外幣）',
    '境外指數股票型基金': '境外指數型',
    '連結式證券指數股票型基金': '連結式指數型',
    '國內成分證券主動式交易所交易基金(股票)': '國內主動型',
    '國外成分證券主動式交易所交易基金(股票)': '海外主動型',
    '國外成分證券主動式交易所交易基金(債券)': '海外主動型（債券）',
    '國外成分證券平衡型指數股票型基金': '平衡型',
    '指數股票型期貨信託基金': STRATEGY_TYPE['COMMODITY'],          # 商品型（黃金/石油/VIX 期貨型 ETF）
    '槓桿/反向指數股票型基金': STRATEGY_TYPE['LEVERAGED_INVERSE'],  # 槓桿反向
}

_cache_by_symbol = None
_cache_fetched_at = None


def _fetch_and_index() -> dict[str, dict]:
    res = requests.get(_FUND_INFO_URL, timeout=15)
    res.raise_for_status()
    out = {}
    for row in res.json():
        symbol = (row.get('基金代號') or '').strip()
        if not symbol:
            continue
        tracking_index = (row.get('標的指數/追蹤指數名稱') or '').strip()
        fund_type = (row.get('基金類型') or '').strip() or None
        out[symbol] = {
            # 主動式基金這欄位固定回傳「不適用」，正規化成 None 讓呼叫端可以統一判斷、
            # 前端再顯示成「主動式基金（無追蹤指數）」而不是照搬 TWSE 的官方用字。
            'tracking_index_name': tracking_index if tracking_index and tracking_index != '不適用' else None,
            'fund_type': fund_type,
            'fund_type_label': FUND_TYPE_LABELS.get(fund_type, fund_type) if fund_type else None,
            'is_active': '主動式' in fund_type if fund_type else False,
            'inception_date': parse_roc_date(row.get('成立日期')),
        }
    return out


def _get_cache() -> dict[str, dict]:
    global _cache_by_symbol, _cache_fetched_at
    now = datetime.now()
    if (_cache_by_symbol is not None and _cache_fetched_at
            and (now - _cache_fetched_at) < timedelta(seconds=_CACHE_TTL_SECONDS)):
        return _cache_by_symbol
    try:
        _cache_by_symbol = _fetch_and_index()
        _cache_fetched_at = now
    except Exception:
        # 抓不到就沿用舊快取（就算過期也比完全沒有好），真的沒有舊快取才讓呼叫端拿到 {}。
        if _cache_by_symbol is None:
            _cache_by_symbol = {}
    return _cache_by_symbol


def get_etf_twse_meta(symbol: str) -> dict | None:
    """查不到（例如太新還沒被 TWSE 這份清單收錄）就回傳 None，呼叫端退回沒有這些欄位的呈現方式。"""
    return _get_cache().get(symbol.upper())
