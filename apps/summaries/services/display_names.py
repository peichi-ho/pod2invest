"""
標的顯示名稱：全平台以大表 IndustryClassification 的 display_name 為準
（中文名優先，沒有才用英文名，都沒有才是 ticker）。

大表約兩千多筆，整張載進記憶體快取，每 10 分鐘重新整理一次，
API 每次回傳不用再查資料庫。查不到的 ticker 回傳 None，由呼叫端決定退回哪個名稱。
"""
import threading
import time

_CACHE_TTL_SECONDS = 600
_lock = threading.Lock()
_cache: dict[str, str] = {}
_cache_loaded_at = 0.0


def _load() -> dict[str, str]:
    from apps.summaries.models import IndustryClassification
    out = {}
    for ticker, zh_name, en_name in (
        IndustryClassification.objects.using("summariesdb").values_list("ticker", "zh_name", "en_name")
    ):
        # TWSE 官方簡稱會在部分公司名稱加「*」（例如「國巨*」「91APP*-KY」），顯示時拿掉
        name = (zh_name or en_name or "").replace("*", "").strip()
        if name:
            out[ticker] = name
    return out


def _get_cache() -> dict[str, str]:
    global _cache, _cache_loaded_at
    if time.time() - _cache_loaded_at < _CACHE_TTL_SECONDS:
        return _cache
    with _lock:
        if time.time() - _cache_loaded_at >= _CACHE_TTL_SECONDS:
            try:
                _cache = _load()
            except Exception:
                pass  # 資料庫暫時連不上就沿用舊快取，不讓整個 API 失敗
            _cache_loaded_at = time.time()
    return _cache


def display_name_for(ticker: str | None) -> str | None:
    """大表裡這檔標的的顯示名稱；ticker 是空的或大表沒收錄就回傳 None。"""
    if not ticker:
        return None
    return _get_cache().get(ticker)
