"""
標的顯示名稱：全平台以大表 IndustryClassification 為準
（中文名優先，沒有才用英文名，都沒有才是 ticker；顯示規則見 format_display_name）。

大表約兩千多筆，整張載進記憶體快取，每 10 分鐘重新整理一次，
API 每次回傳不用再查資料庫。查不到的 ticker 回傳 None，由呼叫端決定退回哪個名稱。
"""
import re
import threading
import time

_CACHE_TTL_SECONDS = 600

# 拿掉「-KY」後會變成一般名詞、看不出是公司的，保留 -KY（使用者確認：4763 材料-KY）
_KEEP_KY = {"材料"}
_lock = threading.Lock()
_cache: dict[str, str] = {}
_entries: list[tuple[str, list[str]]] = []  # 搜尋用：(ticker, [顯示名稱, 中文名, 英文名])
_cache_loaded_at = 0.0


def format_display_name(ticker: str, zh_name: str, en_name: str, market: str, industry_name: str) -> str:
    """
    大表一列 → 前端顯示的名稱。名稱本身存在大表(zh_name 優先，沒有才用 en_name)，
    這裡只套顯示規則，不改資料：
      - TWSE 官方簡稱裡的「*」拿掉（「國巨*」→「國巨」）
      - KY 股拿掉「-KY」（「臻鼎-KY」→「臻鼎」），口語幾乎不講；拿掉後會像一般名詞的保留（_KEEP_KY）
      - 期貨拿掉「期貨」（「黃金期貨」→「黃金」），節目講的是商品本身
      - 美股 ETF 直接顯示代號（QQQ、VOO），大家都講代號
      - 台股 ETF 顯示「代號 名稱」（「0050 元大台灣50」），節目都講代號，只有全名認不出來
    都沒有名稱就顯示 ticker。
    """
    name = (zh_name or en_name or "").replace("*", "").strip()
    stripped = re.sub(r"-?KY$", "", name).strip()
    if stripped not in _KEEP_KY:
        name = stripped
    if industry_name == "期貨" and name.endswith("期貨") and len(name) > 2:
        name = name[:-2]
    if industry_name == "ETF" and market == "US":
        return ticker
    if industry_name == "ETF" and market == "TW":
        code = ticker.split(".")[0]
        return f"{code} {name}" if name else code
    return name or ticker


def _load() -> tuple[dict[str, str], list[tuple[str, list[str]]]]:
    from apps.summaries.models import IndustryClassification
    names, entries = {}, []
    for ticker, zh_name, en_name, market, industry_name in (
        IndustryClassification.objects.using("summariesdb")
        .values_list("ticker", "zh_name", "en_name", "market", "industry_name")
    ):
        if not (zh_name or en_name):
            continue
        display = format_display_name(ticker, zh_name, en_name, market, industry_name)
        names[ticker] = display
        aliases = [display, (zh_name or "").replace("*", "").strip(), en_name or ""]
        entries.append((ticker, list(dict.fromkeys(a for a in aliases if a))))
    return names, entries


def _get_cache() -> dict[str, str]:
    global _cache, _entries, _cache_loaded_at
    if time.time() - _cache_loaded_at < _CACHE_TTL_SECONDS:
        return _cache
    with _lock:
        if time.time() - _cache_loaded_at >= _CACHE_TTL_SECONDS:
            try:
                _cache, _entries = _load()
            except Exception:
                pass  # 資料庫暫時連不上就沿用舊快取，不讓整個 API 失敗
            _cache_loaded_at = time.time()
    return _cache


def search_entries() -> list[tuple[str, list[str]]]:
    """大表全部標的的 (ticker, [顯示名稱, 中文名, 英文名])，給搜尋建議比對用。"""
    _get_cache()
    return _entries


def display_name_for(ticker: str | None) -> str | None:
    """大表裡這檔標的的顯示名稱；ticker 是空的或大表沒收錄就回傳 None。"""
    if not ticker:
        return None
    return _get_cache().get(ticker)
