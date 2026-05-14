# apps/ai_assistant/services/ai_assistant.py
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

from django.conf import settings
from django.core.cache import cache

from google import genai

# =============================================================================
# Data models
# =============================================================================

@dataclass
class PodcastHit:
    episode_title: str
    publish_date: str
    podcaster: str
    episode_number: Optional[str]
    summary: str
    score: float


@dataclass
class StockData:
    ticker: str
    price: Optional[float] = None
    currency: Optional[str] = None
    pe_ttm: Optional[float] = None
    dividend_yield: Optional[float] = None
    market_cap: Optional[float] = None
    timestamp: Optional[str] = None
    source: str = "yfinance"


@dataclass
class ToolResult:
    tool_name: str
    ok: bool
    data: Any
    error: Optional[str] = None


# =============================================================================
# Tool interface
# =============================================================================

class Tool:
    name: str
    description: str

    def run(self, **kwargs) -> Any:
        raise NotImplementedError


class PodcastRetrieverTool(Tool):
    name = "search_podcast_transcript"
    description = "Search podcast knowledge base and return relevant episodes."

    def __init__(self, search_fn: Callable):
        self.search_fn = search_fn

    def run(self, query: str, top_k: int = 5, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        hits = self.search_fn(query, top_k, filters)
        return [asdict(h) for h in hits]


class FinanceTool(Tool):
    name = "get_realtime_stock_data"
    description = "Get stock data via yfinance."

    def __init__(self, fetch_fn: Callable[[str], StockData]):
        self.fetch_fn = fetch_fn

    def run(self, ticker: str) -> Dict[str, Any]:
        data = self.fetch_fn(ticker)
        return asdict(data)


# =============================================================================
# Tool implementations (replace with real ones)
# =============================================================================

_embed_model = None
_reranker_model = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer("BAAI/bge-m3")
    return _embed_model


def _get_reranker_model():
    global _reranker_model
    if _reranker_model is None:
        from sentence_transformers import CrossEncoder
        _reranker_model = CrossEncoder("BAAI/bge-reranker-v2-m3")
    return _reranker_model


def _embed_query(query: str) -> List[float]:
    """將查詢文字用 bge-m3 轉為 dense vector（query 前綴）"""
    vec = _get_embed_model().encode(f"query: {query}", normalize_embeddings=True)
    return vec.tolist()


_MULTI_QUERY_PROMPT = """\
你是一個財經搜尋引擎的查詢最佳化器。

根據以下問題，生成 2 個語意不同但意圖相同的查詢變體，從不同角度搜尋 podcast 知識庫。

規則：
- 每個變體用不同的措辭或切入角度
- 不預設答案方向（不論觀點是看多或看空都要能搜到）
- 用繁體中文

原始問題：{question}

只回傳 JSON：
{{"queries": ["...", "..."]}}
"""


def _expand_queries(query: str) -> List[str]:
    """Multi-Query：用 Gemini 生成 2 個查詢變體，回傳含原始 query 共 3 個。失敗時只回傳原始 query。"""
    try:
        client = get_gemini_client()
        resp = client.models.generate_content(
            model=_get_model_name(),
            contents=_MULTI_QUERY_PROMPT.replace("{question}", query),
            config={"response_mime_type": "application/json", "temperature": 0.3},
        )
        variants = json.loads(resp.text).get("queries", [])
        unique = [v for v in variants if v and v != query][:2]
        return [query] + unique
    except Exception as e:
        logger.warning(f"multi-query expansion failed: {e}")
        return [query]


def _cross_encoder_rerank(candidates: List[Dict[str, Any]], query: str, top_n: int = 30) -> List[Dict[str, Any]]:
    """Cross-encoder 精細重排：對 top_n 個 candidate 做 (query, chunk) 配對評分後重排。"""
    if not candidates:
        return candidates
    try:
        reranker = _get_reranker_model()
        pool = candidates[:top_n]
        rest = candidates[top_n:]
        pairs = [(query, c["chunk_text"]) for c in pool]
        scores = reranker.predict(pairs, show_progress_bar=False)
        for c, s in zip(pool, scores):
            c["cross_score"] = float(s)
        pool.sort(key=lambda x: x["cross_score"], reverse=True)
        return pool + rest
    except Exception as e:
        logger.warning(f"cross-encoder rerank failed: {e}")
        return candidates


def _tokenize_cn(text: str) -> List[str]:
    """中文 BM25 用的 tokenizer：CJK 單字 + bigram + ASCII 詞。"""
    tokens: List[str] = []
    for word in re.findall(r"[a-zA-Z0-9]+", text.lower()):
        if len(word) >= 2:
            tokens.append(word)
    cjk = [c for c in text if "一" <= c <= "鿿"]
    tokens.extend(cjk)
    for i in range(len(cjk) - 1):
        tokens.append(cjk[i] + cjk[i + 1])
    return tokens


def _hybrid_rerank(candidates: List[Dict[str, Any]], query: str, alpha: float = 0.7) -> List[Dict[str, Any]]:
    """BM25 + vector 混合重排；combined_score 寫入每筆 candidate。"""
    if not candidates:
        return candidates
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.warning("rank_bm25 not installed, falling back to vector-only ranking")
        return candidates

    query_tokens = _tokenize_cn(query)
    corpus_tokens = [_tokenize_cn(c["chunk_text"]) for c in candidates]
    bm25 = BM25Okapi(corpus_tokens)
    bm25_scores = bm25.get_scores(query_tokens)

    max_bm25 = float(max(bm25_scores)) if max(bm25_scores) > 0 else 1.0
    for i, c in enumerate(candidates):
        bm25_norm = float(bm25_scores[i]) / max_bm25
        c["combined_score"] = alpha * float(c["score"]) + (1 - alpha) * bm25_norm

    candidates.sort(key=lambda x: x["combined_score"], reverse=True)
    return candidates


def _apply_diversity(candidates: List[Dict[str, Any]], top_k: int, max_per_episode: Optional[int]) -> List[Dict[str, Any]]:
    """每集最多保留 max_per_episode 個 chunk；None 表示不限。"""
    if max_per_episode is None:
        return candidates[:top_k]
    result: List[Dict[str, Any]] = []
    counts: Dict[int, int] = {}
    for c in candidates:
        rid = c["record_id"]
        if counts.get(rid, 0) < max_per_episode:
            result.append(c)
            counts[rid] = counts.get(rid, 0) + 1
        if len(result) >= top_k:
            break
    return result


def search_podcast_transcript(
    query: str,
    top_k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
    n_candidates: Optional[int] = None,
    max_per_episode: Optional[int] = None,
    use_multi_query: bool = True,
    use_reranker: bool = True,
) -> List[PodcastHit]:
    """
    Hybrid (BM25 + vector) search with optional metadata filter and diversity control.

    filters:         entity_companies / entity_people / entity_regions / date_from / date_to
    n_candidates:    candidate pool size per query before reranking（default: max(top_k * 4, 50)）
    max_per_episode: max chunks per episode for diversity（None = no limit）
    use_multi_query: expand query into 3 variants and merge results
    use_reranker:    apply cross-encoder reranking after BM25 hybrid
    """
    from django.db import connections

    if n_candidates is None:
        n_candidates = max(top_k * 4, 50)

    try:
        # Build WHERE（所有 query 變體共用同一組 filter）
        where_parts: List[str] = []
        where_params: List[Any] = []
        if filters:
            entity_conds: List[str] = []
            for company in (filters.get("entity_companies") or []):
                entity_conds.append("entity_companies @> %s::jsonb")
                where_params.append(json.dumps([company], ensure_ascii=False))
            for person in (filters.get("entity_people") or []):
                entity_conds.append("entity_people @> %s::jsonb")
                where_params.append(json.dumps([person], ensure_ascii=False))
            for region in (filters.get("entity_regions") or []):
                entity_conds.append("entity_regions @> %s::jsonb")
                where_params.append(json.dumps([region], ensure_ascii=False))
            if entity_conds:
                where_parts.append("(" + " OR ".join(entity_conds) + ")")
            if filters.get("date_from"):
                where_parts.append("published_at >= %s")
                where_params.append(filters["date_from"])
            if filters.get("date_to"):
                where_parts.append("published_at <= %s")
                where_params.append(filters["date_to"])
            if filters.get("mode"):
                where_parts.append("mode = %s")
                where_params.append(filters["mode"])
            if filters.get("topic_category"):
                where_parts.append("topic_category = %s")
                where_params.append(filters["topic_category"])
            if filters.get("topic_detail"):
                where_parts.append("topic_detail = %s")
                where_params.append(filters["topic_detail"])
            if filters.get("podcaster"):
                where_parts.append("podcaster #>> '{}' ILIKE %s")
                where_params.append(f"%{filters['podcaster']}%")
            for tag in (filters.get("tags") or []):
                tag = tag.strip()
                if tag in TAG_WHITELIST:
                    where_parts.append("tags @> %s::jsonb")
                    where_params.append(json.dumps([tag], ensure_ascii=False))
            if filters.get("source_filename_keyword"):
                where_parts.append("source_filename ILIKE %s")
                where_params.append(f"%{filters['source_filename_keyword']}%")

        def _fetch(vec_str: str, wp: List[str], wparams: List[Any], limit: int) -> List[Any]:
            wc = ("WHERE " + " AND ".join(wp)) if wp else ""
            sql = f"""
                SELECT id, record_id, source_filename, published_at, podcaster, topic,
                       chunk_text, 1 - (embedding <=> %s::vector) AS score
                FROM podcast_embedded_chunks {wc}
                ORDER BY embedding <=> %s::vector LIMIT %s
            """
            with connections["ai_assistant_db"].cursor() as cur:
                cur.execute(sql, [vec_str] + wparams + [vec_str, limit])
                return cur.fetchall()

        # Multi-query 展開（失敗時自動 fallback 成單一 query）
        queries = _expand_queries(query) if use_multi_query else [query]

        # 對每個 query 各自 retrieve，合并取各 chunk 最高分
        merged: Dict[int, Dict[str, Any]] = {}
        for q in queries:
            q_vec = _embed_query(q)
            vec_str = "[" + ",".join(f"{v:.8f}" for v in q_vec) + "]"

            rows = _fetch(vec_str, where_parts, where_params, n_candidates)

            # Fallback if filtered results < top_k
            if where_parts and len(rows) < top_k:
                seen_ids = {r[0] for r in rows}
                for row in _fetch(vec_str, [], [], n_candidates * 2):
                    if row[0] not in seen_ids:
                        rows = list(rows) + [row]
                        seen_ids.add(row[0])
                    if len(rows) >= n_candidates:
                        break

            for r in rows:
                cid, record_id, src, pub, pod_raw, topic, chunk_text, score = r
                score = float(score)
                if isinstance(pod_raw, list):
                    pod_str = "、".join(str(p) for p in pod_raw)
                elif isinstance(pod_raw, str):
                    pod_str = pod_raw
                else:
                    pod_str = ""
                if cid not in merged or score > merged[cid]["score"]:
                    merged[cid] = {
                        "id": cid, "record_id": record_id,
                        "source_filename": src, "published_at": pub,
                        "podcaster": pod_str, "topic": topic,
                        "chunk_text": chunk_text, "score": score,
                    }

        candidates: List[Dict[str, Any]] = list(merged.values())

        # BM25 hybrid rerank → cross-encoder rerank → diversity → slice
        candidates = _hybrid_rerank(candidates, query)
        if use_reranker:
            candidates = _cross_encoder_rerank(candidates, query)
        candidates = _apply_diversity(candidates, top_k, max_per_episode)

        return [
            PodcastHit(
                episode_title=c["topic"] or c["source_filename"] or "",
                publish_date=str(c["published_at"]) if c["published_at"] else "",
                podcaster=c["podcaster"],
                episode_number=None,
                summary=c["chunk_text"],
                score=c.get("cross_score", c.get("combined_score", c["score"])),
            )
            for c in candidates
        ]

    except Exception as e:
        logger.warning(f"pgvector search failed: {e}")
        return []


def get_realtime_stock_data(ticker: str) -> StockData:
    ttl = int(getattr(settings, "STOCK_CACHE_TTL_SECONDS", 60))
    cache_key = f"ai_assistant:stock:{ticker.upper()}"
    cached = cache.get(cache_key)
    if cached:
        return StockData(**cached)

    import yfinance as yf

    tk = yf.Ticker(ticker)
    info = tk.fast_info or {}
    price = info.get("last_price") or info.get("lastPrice")
    currency = info.get("currency")

    full = tk.info or {}
    pe = full.get("trailingPE")
    div_yield = full.get("dividendYield")
    mcap = full.get("marketCap")

    data = StockData(
        ticker=ticker.upper(),
        price=price,
        currency=currency,
        pe_ttm=pe,
        dividend_yield=div_yield,
        market_cap=mcap,
        timestamp=None,
    )

    cache.set(cache_key, asdict(data), ttl)
    return data


# =============================================================================
# Gemini adapter
# =============================================================================

TAG_WHITELIST = {
    "總體經濟", "半導體", "新聞", "美股", "ETF",
    "金融市場", "人工智慧", "訪談", "台股", "個股",
    "投資策略", "電動車", "深度分析", "港股", "債券",
    "產業分析", "能源", "投資觀點", "加密貨幣", "貴金屬",
    "公司分析", "金融", "案例研究", "外匯",
    "科技趨勢", "生技醫療", "總體債市",
    "政策與地緣政治", "消費科技",
    "雲端 / SaaS", "區塊鏈 / 加密貨幣", "電商", "媒體娛樂", "製造業", "房地產",
}

PLANNER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "use_tools": {"type": "boolean"},
        "out_of_scope": {"type": "boolean"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "enum": ["search_podcast_transcript", "get_realtime_stock_data"],
                    },
                    "args_json": {"type": "string"},
                },
                "required": ["tool", "args_json"],
            },
        },
        "fallback_to_finance_llm": {"type": "boolean"},
        "final_answer_style": {"type": "string"},
    },
    "required": ["use_tools", "out_of_scope", "actions", "fallback_to_finance_llm", "final_answer_style"],
}

_client: Optional[genai.Client] = None


def get_gemini_client() -> genai.Client:
    global _client
    if _client is not None:
        return _client

    _client = genai.Client(
        vertexai=True,
        project=settings.VERTEX_PROJECT_ID,
        location=settings.VERTEX_LOCATION,
    )
    return _client


def _get_model_name(explicit: Optional[str] = None) -> str:
    return (
        explicit
        or getattr(settings, "GEMINI_MODEL", None)
        or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    )


def _build_history_contents(history: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """將對話歷史轉換為 Gemini 多輪格式（user / model 交替）"""
    contents = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    return contents


def call_gemini_json(
    system_prompt: str,
    user_prompt: str,
    history: Optional[List[Dict[str, str]]] = None,
    model: Optional[str] = None,
) -> str:
    """Planner：強制 JSON 輸出，將近期歷史摘要注入 system prompt 供指代消解"""
    client = get_gemini_client()
    use_model = _get_model_name(model)

    history_context = ""
    if history:
        recent = history[-6:]  # 最近 3 輪（user + assistant 各一）
        lines = []
        for msg in recent:
            prefix = "使用者" if msg["role"] == "user" else "助理"
            lines.append(f"{prefix}：{msg['content']}")
        history_context = "\n\n近期對話記錄（供指代消解參考）：\n" + "\n".join(lines)

    contents = [
        {"role": "user", "parts": [{"text": f"[SYSTEM]\n{system_prompt}{history_context}"}]},
        {"role": "user", "parts": [{"text": f"[USER]\n{user_prompt}"}]},
    ]

    resp = client.models.generate_content(
        model=use_model,
        contents=contents,
        config={
            "response_mime_type": "application/json",
            "response_schema": PLANNER_SCHEMA,
            "temperature": 0.0,
        },
    )
    return resp.text


def call_gemini_text(
    system_prompt: str,
    user_prompt: str,
    history: Optional[List[Dict[str, str]]] = None,
    model: Optional[str] = None,
) -> str:
    """Final/Finance QA：Gemini 多輪格式，帶入完整對話歷史"""
    client = get_gemini_client()
    use_model = _get_model_name(model)

    # system 用 user/model 對話模擬（Gemini 無原生 system role）
    contents: List[Dict[str, Any]] = [
        {"role": "user", "parts": [{"text": f"[SYSTEM]\n{system_prompt}"}]},
        {"role": "model", "parts": [{"text": "Understood."}]},
    ]

    # 帶入完整歷史
    if history:
        contents.extend(_build_history_contents(history))

    # 當前問題
    contents.append({"role": "user", "parts": [{"text": user_prompt}]})

    resp = client.models.generate_content(
        model=use_model,
        contents=contents,
        config={"temperature": 0.2},
    )
    return resp.text


# =============================================================================
# Orchestrator
# =============================================================================

class Orchestrator:
    def __init__(
        self,
        tools: List[Tool],
        planner_llm: Callable,
        finance_qa_llm: Callable,
        final_llm: Callable,
    ):
        self.tool_map = {t.name: t for t in tools}
        self.planner_llm = planner_llm
        self.finance_qa_llm = finance_qa_llm
        self.final_llm = final_llm

    def plan(self, user_input: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        system = f"""
        你是一個財經助理系統中的規劃器，根據使用者問題，判斷：
        1. 是否超出財經領域
        2. 是否需要呼叫工具
        3. 若需要，該呼叫哪些工具與參數
        4. 若不需要工具，是否交由財經知識模型直接回答

        你只能輸出符合 schema 的 JSON，不能輸出任何額外文字。

        可用工具：
        {list(self.tool_map.keys())}

        工具選擇原則：
        1. 問股價、本益比、股息率、市值、ticker 等即時股票資料時，使用 get_realtime_stock_data。
        2. 問 podcast 是否談過某主題、某位來賓觀點、某集內容時，使用 search_podcast_transcript。
        3. 一般財經知識、投資觀念、名詞解釋、原理比較，且不依賴即時資料時，不使用工具。

        決策規則：
        1. 如果問題與財經、投資、股票、ETF、基金、總經、利率、公司財報、估值、podcast 財經內容無關，out_of_scope=true。
        2. 如果問題需要即時或外部資料（如股價、本益比、股息率、ticker），use_tools=true。
        3. 如果問題是在問 podcast 是否談過某主題、某人觀點、某集內容，use_tools=true。
        4. 如果問題屬於一般財經知識，use_tools=false 且 fallback_to_finance_llm=true。
        5. 若 use_tools=true，fallback_to_finance_llm=false。
        6. 若 use_tools=true，actions 不得為空。
        7. actions 只放必要工具，避免多餘呼叫。
        8. 若問題包含公司名稱但未提供 ticker，仍應推測常見 ticker（如 Apple→AAPL，台積電→TSM）。
        9. 若問題同時涉及即時資料與投資判斷（如是否值得買），仍應使用工具。
        10. 若問題可用一般知識完整回答，禁止使用工具。
        11. args_json 必須是合法 JSON 字串，且包含必要參數（如 ticker 或 query）。
        12. 若使用者用代名詞（它、這支、剛才那檔），請參考近期對話記錄推斷實際指稱對象。

        search_podcast_transcript 的 args_json 格式：
        {{
          "query": "搜尋字串（必填）",
          "filters": {{
            "entity_companies":       ["公司或股票名稱"],
            "entity_people":          ["人名"],
            "entity_regions":         ["國家或地區"],
            "date_from":              "YYYY-MM-DD",
            "date_to":                "YYYY-MM-DD",
            "podcaster":              "節目名稱",
            "tags":                   ["標籤"],
            "source_filename_keyword":"集數關鍵字"
          }}
        }}

        filters 填寫規則（重要）：
        - entity_companies：只填真實公司名或股票名稱（如台積電、NVIDIA、聯發科）。「美股」、「台股」、「科技巨頭」、「AI公司」等概念詞不算公司名，不要填入。
        - entity_people：只填真實人名（如黃仁勳、川普）。職稱或角色名稱不算。
        - entity_regions：只填真實國家或地區名稱（如美國、台灣、中國）。「美股」、「台股」是市場代稱，不是地區，不要填入。
        - podcaster：若使用者明確提及節目名稱或主持人名稱（如「下班經濟學」、「股癌」），填入此欄。模糊描述不填。
        - tags：只能從以下白名單中選，不得自創。根據問題主題填入最相關的 1–3 個：
          {sorted(TAG_WHITELIST)}
          「美股」、「台股」這類詞若在白名單中才能填，不在白名單不要填。
        - source_filename_keyword：若使用者提到某一集的標題關鍵字（如「法說會」、「ETF 比較」），填入此欄做模糊搜尋。
        - 若問題沒有明確提及上述實體，該欄位省略，不要猜測。
        - 若問題有時間範圍（如「今年」、「最近三個月」、「2024 年」），填入 date_from / date_to。
        - 若無任何明確 filter，filters 整個省略。
        """
        raw = self.planner_llm(system, user_input, history)
        return self._safe_json_parse(raw)

    def run_tools(self, actions: List[Dict[str, Any]], user_mode: Optional[str] = None) -> List[ToolResult]:
        results: List[ToolResult] = []

        for act in actions:
            tool_name = act.get("tool")
            args_json = act.get("args_json", "{}")

            try:
                args = json.loads(args_json) if isinstance(args_json, str) else (args_json or {})
            except json.JSONDecodeError:
                args = {}

            # Inject mode filter so search is scoped to the user's content tier
            if user_mode and tool_name == "search_podcast_transcript":
                args.setdefault("filters", {})["mode"] = user_mode

            tool = self.tool_map.get(tool_name)
            if not tool:
                results.append(
                    ToolResult(tool_name=tool_name or "UNKNOWN", ok=False, data=None, error="Tool not found")
                )
                continue

            try:
                data = tool.run(**args)
                results.append(ToolResult(tool_name=tool.name, ok=True, data=data))
            except Exception as e:
                results.append(ToolResult(tool_name=tool.name, ok=False, data=None, error=str(e)))

        return results

    def answer(self, user_input: str, history: Optional[List[Dict[str, str]]] = None, user_mode: Optional[str] = None) -> str:
        history = history or []
        plan = self.plan(user_input, history)

        if bool(plan.get("out_of_scope")):
            return "我是專注於財經領域的 AI 助理，無法回答這個問題。如果您有股票、基金、投資或財經相關的問題，歡迎隨時提問！"

        use_tools = bool(plan.get("use_tools"))
        actions = plan.get("actions", []) or []
        fallback = bool(plan.get("fallback_to_finance_llm"))

        if not use_tools and fallback:
            system = """
            你是一位專業的財經知識助理，請用繁體中文回答。
            目標是提供「正確、簡潔、可操作」的回答。

            回答規則：
            1. 先直接回答問題核心，不要先鋪陳。
            2. 優先給出結論，再補一句最重要的理由或風險提醒。
            3. 若問題資訊不足，明確指出缺少的關鍵資訊，不要自行虛構。
            4. 若牽涉價格、報酬率、時間點、政策、法規等可能變動資訊，避免假裝精確，改用保守表述。
            5. 不要自稱 AI，不要寫多餘寒暄，不要重述題目。
            6. 若使用者問的是延續前一個話題的問題，請結合對話歷史作答。
            """
            return self.finance_qa_llm(system, user_input, history)

        tool_results = self.run_tools(actions, user_mode=user_mode)
        tool_context = {"user_input": user_input, "tool_results": [asdict(r) for r in tool_results]}

        system = """
        你是一位專業的財經助理，請一律使用繁體中文回答。

        你的任務：
        1. 先判斷工具結果是否足以回答使用者問題。
        2. 若足夠，優先根據工具結果直接回答。
        3. 若不足，明確指出不足處，並僅補充不依賴即時資料的通用財經知識。
        4. 若工具失敗，簡短說明限制並提供可行替代方向。

        回答規則：
        1. 優先回答使用者真正想知道的結論，不要只是摘要工具資料。
        2. 若工具結果明確，必須優先採用。
        3. 若工具結果互相衝突，指出衝突與限制。
        4. 不可虛構即時數字、價格、公告、新聞、法規或日期。
        5. 若涉及投資建議，避免保證報酬，應說明主要風險與判斷依據。
        6. 若無法明確回答，要直接說明原因。
        7. 若使用者問的是延續前一個話題的問題，請結合對話歷史作答。

        內容不足時的透明化規則（重要）：
        8. 若 retrieved chunks 的內容與問題明顯不相關（跑題、只沾到邊），主動說明：
           「Podcast 中目前找到的內容與這個問題的關聯性較低，以下回答僅供參考。」
        9. 若問題問的是某公司或主題，但 chunks 完全沒有提及，明確說：
           「這個主題在目前收錄的 Podcast 中沒有找到直接討論。」
        10. 若問題過於寬泛（例如只提概念詞、未指定公司或時間），在回答後補一句建議：
            「若要取得更精準的 Podcast 觀點，可以嘗試在問題中加入具體的公司名稱、人名或時間範圍。」

        輸出風格：
        - 一般用 1~3 段短文
        - 必要時才用條列
        - 不要輸出 JSON
        - 不要逐欄翻譯工具結果
        - 不要自稱 AI 或模型
        """

        user = (
            f"請根據以下資訊回答使用者問題。\n"
            f"若工具結果足夠，直接回答；若不足，清楚說明不足處，並只補充通用財經知識。\n\n"
            f"使用者問題：\n{user_input}\n\n"
            f"工具結果 JSON：\n"
            + json.dumps(tool_context, ensure_ascii=False, indent=2)
        )
        return self.final_llm(system, user, history)

    @staticmethod
    def _safe_json_parse(raw: str) -> Dict[str, Any]:
        raw = (raw or "").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*?\}", raw, flags=re.S)
            if not m:
                return {
                    "use_tools": False,
                    "actions": [],
                    "fallback_to_finance_llm": True,
                    "final_answer_style": "zh-TW",
                }
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return {
                    "use_tools": False,
                    "actions": [],
                    "fallback_to_finance_llm": True,
                    "final_answer_style": "zh-TW",
                }


# =============================================================================
# Singleton agent for Django
# =============================================================================

_agent: Optional[Orchestrator] = None


def get_agent() -> Orchestrator:
    global _agent
    if _agent is not None:
        return _agent

    tools: List[Tool] = [
        PodcastRetrieverTool(search_fn=search_podcast_transcript),
        FinanceTool(fetch_fn=get_realtime_stock_data),
    ]

    _agent = Orchestrator(
        tools=tools,
        planner_llm=call_gemini_json,
        finance_qa_llm=call_gemini_text,
        final_llm=call_gemini_text,
    )
    return _agent


def answer_user(query: str, history: Optional[List[Dict[str, str]]] = None, user_mode: Optional[str] = None) -> str:
    """
    Public entry for Django views.
    history: [{"role": "user"|"assistant", "content": "..."}]
    user_mode: "pro" | "novice" — pre-filters podcast search to matching content tier
    """
    return get_agent().answer(query, history or [], user_mode=user_mode)
