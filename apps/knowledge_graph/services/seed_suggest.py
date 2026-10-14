# apps/knowledge_graph/services/seed_suggest.py
"""
探索頁「查詢標的」輸入框的搜尋建議。

使用者輸入文字 → 從圖譜節點裡找「名稱（或股票代號）包含該文字」、而且是投資標的的節點。
投資標的 = 對得到股票代號的上市公司或 ETF；概念、產業、產品、未上市公司（OpenAI 等）、
指數/商品/外匯都不顯示。

「是不是投資標的」靠股票代號對照判斷，沿用 basket_selection.filter_node_type 的定義
（同一套規則，建議清單才不會推薦出 basket 本身會排除的節點），差別只在：
  - 對照表一次批次查詢（不是逐個節點查資料庫），整張節點表約 1~2 秒建完，結果快取 10 分鐘
  - 名稱長得像股票代號的節點（META、HBM…）要實際查得到股價才算，結果永久快取在程序內，
    而且只驗證「這次輸入符合的前幾名」，不會一次打幾百次 Yahoo

已知限制（驗證圖譜時發現的，詳見 kg_eval_results/）：
  - 查不到代號的真上市公司（沃爾瑪、豐田、英業達…）會被當成非標的而不顯示，要補進
    apps.summaries 的 TickerMap 才會出現。
  - 概念名詞被誤判成冷門股票代號的情況（HBM、PCB、CPU…）靠 NOT_A_STOCK_NODES 人工清單擋掉，
    之後新增的誤判要補進清單。
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from django.db import connections

from .basket_selection import _VERIFIED_FALSE_POSITIVE_NODES, _is_non_company_ticker

SUGGEST_LIMIT = 8
_INDEX_TTL_SECONDS = 600
_LIVE_CHECK_TIMEOUT = 8

# 人工確認過：這些節點指的不是 Yahoo 上同名代號那家公司（產品/材料名詞、未上市公司、
# 品牌名等被「全大寫≤6碼就當代號」的規則誤判），或是跟另一個節點是同一家公司的不同掛牌。
NOT_A_STOCK_NODES = {
    "ASIC", "CCL", "EVG", "HBM", "NSP", "PCB", "AURA", "MG", "MOMO", "VIVO", "UG", "SIG", "AZ",
    "SOXX", "台積電ADR", "CPU", "LPX", "VERA", "DPC", "TOK", "AES", "AS", "CSB", "GT", "S", "ABTC",
    # 建議清單上線前，把「名稱長得像代號、也查得到股價」的 70 個節點對照圖譜原始說明逐一檢查，又找到這些
    "A",    # OpenAI 的代稱
    "B",    # 台灣的 ABF 概念股
    "CC",   # 電動車款名稱
    "GL",   # AI 模型名稱
    "GPC",  # AI 模型名稱
    "NEO",  # 機器人產品（未上市）
    "OLED", # 面板技術
    "SK",   # 韓國 SK 集團
    "TEL",  # 說明指東京威力科創，不是 Yahoo 上的 TE Connectivity
    "UEC",  # Ultra Ethernet Consortium（標準組織）
    "EML",  # 說明對不上 Yahoo 上同名公司
}

_SHAPE_ONLY_RE = re.compile(r"^[A-Z]{1,6}$")     # 名稱本身就長得像美股代號

_lock = threading.Lock()
_index: dict = {"built_at": 0.0, "items": []}
_live_cache: dict[str, bool] = {}                  # ticker → 查得到股價嗎（只存確定的結果）


def _market_label(ticker: str, is_etf: bool) -> str:
    if is_etf:
        return "ETF"
    if ticker.endswith((".TW", ".TWO")):
        return "台股"
    if "." in ticker:
        return "海外"
    return "美股"


def _resolve_many(names: list[str]) -> dict[str, str]:
    """
    節點名稱 → 股票代號，批次版。規則層直接判（不查資料庫），剩下的一次查 TickerMap。
    逐個呼叫 apps.summaries 的 resolve_ticker() 會每個節點打一次資料庫，2000 多個節點要一兩分鐘。
    """
    from apps.summaries.services.backtesting import _rule_based_ticker

    out: dict[str, str] = {}
    rest: list[str] = []
    for n in names:
        try:
            t = _rule_based_ticker(n)
        except Exception:
            t = None
        if t:
            out[n] = t
        else:
            rest.append(n)
    try:
        from apps.summaries.models import TickerMap
        rows = TickerMap.objects.using("summariesdb").filter(asset_name__in=rest).values_list("asset_name", "ticker")
        for name, ticker in rows:
            if ticker:
                out[name] = ticker
    except Exception:
        pass
    return out


def _build_index() -> list[dict]:
    with connections["knowledge_graphdb"].cursor() as cur:
        cur.execute("SELECT name, industry FROM nodes")
        nodes = [(n, i or "") for n, i in cur.fetchall()]
        cur.execute(
            "SELECT name, COUNT(*) FROM (SELECT source AS name FROM links UNION ALL SELECT target FROM links) t GROUP BY name"
        )
        mentions = dict(cur.fetchall())

    tickers = _resolve_many([n for n, _ in nodes])
    best: dict[str, dict] = {}
    for name, industry in nodes:
        ticker = tickers.get(name)
        if not ticker or _is_non_company_ticker(ticker):
            continue
        if name in _VERIFIED_FALSE_POSITIVE_NODES or name in NOT_A_STOCK_NODES:
            continue
        item = {
            "name": name,
            "ticker": ticker,
            "code": ticker.split(".")[0],
            "market": _market_label(ticker, industry == "ETF/基金"),
            "shape_only": bool(_SHAPE_ONLY_RE.match(name)) and ticker == name,
            "mentions": mentions.get(name, 0),
        }
        # 同一檔股票有好幾個別名節點（例如「奇鋐」和「3017」）時，只留被提到最多的那個
        cur_best = best.get(ticker)
        if cur_best is None or item["mentions"] > cur_best["mentions"]:
            best[ticker] = item
    return list(best.values())


def _get_index() -> list[dict]:
    now = time.time()
    if now - _index["built_at"] < _INDEX_TTL_SECONDS and _index["items"]:
        return _index["items"]
    with _lock:
        if time.time() - _index["built_at"] < _INDEX_TTL_SECONDS and _index["items"]:
            return _index["items"]
        _index["items"] = _build_index()
        _index["built_at"] = time.time()
        return _index["items"]


def _live_check(ticker: str) -> bool | None:
    """True/False = 確定查得到/查不到股價；None = 網路等原因沒辦法確定（這次不顯示，也不快取）。"""
    try:
        import yfinance as yf
        return not yf.Ticker(ticker).history(period="5d").empty
    except Exception:
        return None


def _verify_shape_only(tickers: list[str]) -> None:
    todo = [t for t in dict.fromkeys(tickers) if t not in _live_cache]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=min(8, len(todo))) as ex:
        futures = {t: ex.submit(_live_check, t) for t in todo}
        for t, f in futures.items():
            try:
                ok = f.result(timeout=_LIVE_CHECK_TIMEOUT)
            except Exception:
                ok = None
            if ok is not None:
                _live_cache[t] = ok


def suggest_seeds(query: str, limit: int = SUGGEST_LIMIT) -> list[dict]:
    """
    回傳最多 limit 筆 {"name","ticker","code","market"}。
    符合規則：名稱包含輸入文字，或股票代號以輸入文字開頭（不分大小寫）。
    排序：名稱開頭符合 > 代號開頭符合 > 名稱中間符合；同一級別內被圖譜提到越多越前面。
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    hits = []
    for it in _get_index():
        name_l, code_l = it["name"].lower(), it["code"].lower()
        if name_l.startswith(q):
            rank = 0
        elif code_l.startswith(q):
            rank = 1
        elif q in name_l:
            rank = 2
        else:
            continue
        hits.append((rank, -it["mentions"], it["name"], it))
    hits.sort(key=lambda h: h[:3])

    window = [h[3] for h in hits[: limit * 3]]       # 多取一些，扣掉驗證不過的之後還湊得滿
    _verify_shape_only([it["ticker"] for it in window if it["shape_only"]])
    ok = [it for it in window if not it["shape_only"] or _live_cache.get(it["ticker"])]
    return [{k: it[k] for k in ("name", "ticker", "code", "market")} for it in ok[:limit]]


def warm_async() -> None:
    """使用者打開探索頁時先在背景把索引建好，輸入第一個字時就不用等 3~4 秒。"""
    if _index["items"] and time.time() - _index["built_at"] < _INDEX_TTL_SECONDS:
        return

    def _run():
        try:
            _get_index()
        except Exception:
            pass
        finally:
            connections.close_all()     # 背景執行緒用完要關掉自己開的資料庫連線

    threading.Thread(target=_run, daemon=True).start()
