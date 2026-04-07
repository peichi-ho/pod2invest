# apps/ai_assistant/services/llm.py
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List, Optional

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

    def __init__(self, search_fn: Callable[[str, int], List[PodcastHit]]):
        self.search_fn = search_fn

    def run(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        hits = self.search_fn(query, top_k)
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
# Example tool implementations (replace with yours)
# =============================================================================

def search_podcast_transcript(query: str, top_k: int = 5) -> List[PodcastHit]:
    """
    TODO: Replace with Pinecone / vector DB retrieval.
    Return a list of PodcastHit.
    """
    demo_hits: List[PodcastHit] = []
    if any(k in query.lower() for k in ["巴菲特", "buffett", "fed", "fomc", "利率"]):
        demo_hits.append(
            PodcastHit(
                episode_title="從巴菲特談價值投資：護城河與估值",
                publish_date="2025-11-18",
                podcaster="Alpha Finance Podcast",
                episode_number="EP.128",
                summary="討論護城河、DCF、以及如何看待市場情緒與長期現金流。",
                score=0.86,
            )
        )
    return demo_hits[:top_k]


def get_realtime_stock_data(ticker: str) -> StockData:
    """
    yfinance call can be slow / rate-limited.
    We'll cache for a short time (default 60s) to keep Django responsive.
    """
    # 簡單快取：同一 ticker 60 秒內不重打
    ttl = int(getattr(settings, "STOCK_CACHE_TTL_SECONDS", 60))
    cache_key = f"ai_assistant:stock:{ticker.upper()}"
    cached = cache.get(cache_key)
    if cached:
        return StockData(**cached)

    import yfinance as yf

    tk = yf.Ticker(ticker)

    # fast_info is quick but fields vary
    info = tk.fast_info or {}
    price = info.get("last_price") or info.get("lastPrice")
    currency = info.get("currency")

    # tk.info can be slow
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

    cache.set(cache_key, asdict(data), ttl)  # [ADDED]
    return data


# =============================================================================
# Gemini adapter (LAZY INIT)
# =============================================================================

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
    """
    [CHANGED] Lazy init: do NOT create client at import time.
    """
    global _client
    if _client is not None:
        return _client

    api_key = getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY. Set it in settings.py or env var.")

    _client = genai.Client(api_key=api_key)
    return _client


def _get_model_name(explicit: Optional[str] = None) -> str:
    return (
        explicit
        or getattr(settings, "GEMINI_MODEL", None)
        or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    )


def call_gemini_json(system_prompt: str, user_prompt: str, model: Optional[str] = None) -> str:
    """
    Planner: force JSON output (schema-guided).
    """
    client = get_gemini_client()
    use_model = _get_model_name(model)

    resp = client.models.generate_content(
        model=use_model,
        contents=[
            {"role": "user", "parts": [{"text": f"[SYSTEM]\n{system_prompt}"}]},
            {"role": "user", "parts": [{"text": f"[USER]\n{user_prompt}"}]},
        ],
        config={
            "response_mime_type": "application/json",
            "response_schema": PLANNER_SCHEMA,
            "temperature": 0.0,
        },
    )
    return resp.text


def call_gemini_text(system_prompt: str, user_prompt: str, model: Optional[str] = None) -> str:
    """
    Final/Finance QA: normal text output.
    """
    client = get_gemini_client()
    use_model = _get_model_name(model)  # [CHANGED]

    resp = client.models.generate_content(
        model=use_model,
        contents=[
            {"role": "user", "parts": [{"text": f"[SYSTEM]\n{system_prompt}"}]},
            {"role": "user", "parts": [{"text": f"[USER]\n{user_prompt}"}]},
        ],
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
        planner_llm: Callable[[str, str], str],
        finance_qa_llm: Callable[[str, str], str],
        final_llm: Callable[[str, str], str],
    ):
        self.tool_map = {t.name: t for t in tools}
        self.planner_llm = planner_llm
        self.finance_qa_llm = finance_qa_llm
        self.final_llm = final_llm

    def plan(self, user_input: str) -> Dict[str, Any]:
        system = system = f"""
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

        """
        raw = self.planner_llm(system, user_input)
        return self._safe_json_parse(raw)

    def run_tools(self, actions: List[Dict[str, Any]]) -> List[ToolResult]:
        results: List[ToolResult] = []

        for act in actions:
            tool_name = act.get("tool")
            args_json = act.get("args_json", "{}")

            try:
                args = json.loads(args_json) if isinstance(args_json, str) else (args_json or {})
            except json.JSONDecodeError:
                args = {}

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

    def answer(self, user_input: str) -> str:
        plan = self.plan(user_input)

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
            """
            return self.finance_qa_llm(system, user_input)

        tool_results = self.run_tools(actions)
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
        return self.final_llm(system, user)

    @staticmethod
    def _safe_json_parse(raw: str) -> Dict[str, Any]:
        """
        Attempt strict JSON parse; if LLM returns extra text, extract the first JSON object.
        """
        raw = (raw or "").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # 只取第一段可能的 JSON object
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
    """
    [CHANGED] Singleton for Django process: avoid rebuilding tools every request.
    """
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


def answer_user(query: str) -> str:
    """
    Public entry for Django views.
    """
    return get_agent().answer(query)