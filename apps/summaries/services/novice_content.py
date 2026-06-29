# apps/summaries/services/novice_content.py
"""
Novice-mode 的局部生成邏輯。

流程：
  1. 接收 pro_result（已跑完）與完整逐字稿
  2. 只讓 LLM 輸出 investment_takeaways + arguments（novice 風格）
  3. 其餘欄位（one_sentence_summary / classification / tags / entities /
     outlook_calls / arguments.evidence_timestamps）全部直接從 pro_result 複製

好處：
  - novice 讀完整逐字稿，品質不受 pro 壓縮摘要影響
  - 避免重複生成 classification / entities 等結構性欄位，確保一致
"""

import copy
import json
from pathlib import Path
from typing import List, Optional

from google import genai

from .gemini import gemini_generate_with_retry, sanitize_json_text, repair_to_valid_json


# ─────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────

_NOVICE_SYSTEM = (
    "你是一個台灣金融投資 Podcast 內容解說助理，"
    "專門為沒有投資背景的初學者整理節目重點。\n"
    "輸出風格：親切易懂，專有名詞一定要用白話解釋（例如 CoWoS = 台積電先進封裝技術），"
    "讓完全沒有投資背景的人也能看懂。"
)


def _build_prompt(forced_topics: List[str]) -> str:
    topics_lines = "\n".join(f"  - 「{t}」" for t in forced_topics)
    return f"""【最高優先規則：主題清單鎖定】
本集 Podcast 討論的主題清單（arguments 必須嚴格對應，不可增刪，一字不差）：
{topics_lines}

【任務】
請針對上方每個主題，以「完全不懂投資的初學者」角度整理兩個部分：

━━ 一、investment_takeaways（初學者版本）━━━━━━━━━━━━━━━━━━━━━━
bullish / bearish / watchlist 每筆格式：「[具體標的] — [一句白話理由，不用術語]」
  ✓ 「台積電 — AI 晶片需求大增，公司接單滿載，今年獲利預計大幅成長」
  ✓ 「記憶體族群 — 供需開始反轉，漲價趨勢已確認，未來幾季業績有望改善」
  ✗ 「台積電 — CoWoS 訂單滿載」（術語沒解釋，新手根本看不懂 CoWoS）
  ✗ 「市場整體看好」（太模糊，沒有具體標的和理由）
bullish：podcaster 明確看漲的股票、ETF、產業或指數
bearish：明確看跌或提出風險警示的標的
watchlist：尚無明確方向但值得追蹤的標的
podcaster_stance：整集立場，只能從四個選一：看多 / 看空 / 觀望 / 混合/視情況
沒有對應內容就給空陣列，不要硬填

━━ 二、arguments（每個主題各一筆）━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- topic：與上方清單完全相同，一字不差，連標點符號都不可改，不可加括號或股票代碼
- position：說清楚 podcaster 的方向和「為什麼」，讓新手能理解脈絡
    ✓ 「看好台積電，因為 AI 晶片需求持續拉動先進封裝訂單，產能已排到明年，競爭對手短期難追上」
    ✗ 「看多」（太短，沒有原因）
    ✗ 「討論了各面向」（沒有立場）
- summary：完整段落（150–200 字），用說故事的方式依序寫：
    1) 這個主題的背景是什麼（白話說清楚，遇到專有名詞一定要用括號解釋）
    2) 發生了什麼事、為什麼普通人要在意
    3) 對一般投資人代表什麼意義（具體但不給買賣建議）
    4) 有什麼風險或不確定性
    語氣親切易懂，避免術語堆疊，目標讀者是完全沒投資背景的人
- key_data：只收明確的數字、百分比、價格、日期，純文字陳述不可放入，沒有數字就給 []
    每筆三個欄位都必須填，不可留空：
      label   = 這個數字的指標名稱（例如：「預估EPS」「目標股價」「毛利率」）
      value   = 數字本身（例如：「95元」「59.5%」「1560元」「2024Q4」）
      context = 白話說明這個數字代表什麼意義
    ✓ {{"label":"預估EPS","value":"95元","context":"代表台積電今年每股預計賺95元，比去年大幅成長"}}
    ✗ {{"label":"","value":"台股衝破28000點","context":""}}（value 是文字不是數字，且 label/context 是空的）
- related_concepts：與此主題相關的財經概念名稱（字串陣列），沒有就給 []

【輸出格式】
只輸出合法 JSON（不要 ```json，不要任何多餘文字）：
{{
  "investment_takeaways": {{
    "bullish": [],
    "bearish": [],
    "watchlist": [],
    "podcaster_stance": "看多|看空|觀望|混合/視情況"
  }},
  "arguments": [
    {{
      "topic": "（與清單完全一致）",
      "position": "...",
      "summary": "...",
      "key_data": [{{"label": "...", "value": "...", "context": "..."}}],
      "related_concepts": []
    }}
  ]
}}
"""


# ─────────────────────────────────────────────────────────
# Generation
# ─────────────────────────────────────────────────────────

def generate_novice_content(
    *,
    client: genai.Client,
    model: str,
    inline_text: str,
    forced_topics: List[str],
    raw_save_path: Optional[Path] = None,
) -> dict:
    """
    讀完整逐字稿，只輸出 investment_takeaways + arguments（novice 風格）。
    回傳 dict，keys: investment_takeaways, arguments
    """

    def append_raw(title: str, content: str):
        if not raw_save_path:
            return
        with open(raw_save_path, "a", encoding="utf-8") as f:
            f.write(f"{title}\n{content}\n\n")

    content_prompt = _build_prompt(forced_topics)

    # 因為輸出 schema 比全量小很多，可以放更長的逐字稿進去
    head = inline_text[:28000]
    tail = inline_text[-10000:] if len(inline_text) > 38000 else ""
    excerpt = head + ("\n\n---\n（中略，後段摘要）\n---\n\n" + tail if tail else "")

    prompt = (
        f"===SYSTEM===\n{_NOVICE_SYSTEM}\n===END SYSTEM===\n\n"
        f"===任務說明===\n{content_prompt}\n===END 任務說明===\n\n"
        "===逐字稿（已帶入時間戳，格式：（m:ss）內容）===\n"
        f"{excerpt}\n"
        "===END 逐字稿===\n\n"
        "請根據以上逐字稿，輸出合法 JSON（只輸出 JSON，不要加任何說明）："
    )

    resp = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=prompt,
        temperature=0.3,
        max_output_tokens=8000,
        max_tries=6,
    )
    raw = (getattr(resp, "text", "") or "")
    append_raw("===NOVICE CONTENT RAW===", raw)

    clean = sanitize_json_text(raw)
    if not clean.strip():
        raise RuntimeError("novice content generation 回傳空回應")

    try:
        obj = json.loads(clean)
    except json.JSONDecodeError:
        obj = repair_to_valid_json(client, model, clean)

    return obj


# ─────────────────────────────────────────────────────────
# Merge
# ─────────────────────────────────────────────────────────

def merge_novice_content(pro_result: dict, novice_content: dict) -> dict:
    """
    以 pro_result 為底，把 novice 的兩個欄位蓋上去：
      - investment_takeaways  → 來自 novice
      - arguments[].position / summary / key_data / related_concepts → 來自 novice
      - arguments[].topic / evidence_timestamps → 保留 pro
      - one_sentence_summary / classification / tags / entities / outlook_calls → 保留 pro
    """
    novice = copy.deepcopy(pro_result)

    # 1. 替換 investment_takeaways
    if novice_content.get("investment_takeaways"):
        novice["investment_takeaways"] = novice_content["investment_takeaways"]

    # 2. 建立 novice arguments 的 topic → arg 對照表
    novice_args_by_topic: dict[str, dict] = {}
    for arg in (novice_content.get("arguments") or []):
        topic = (arg.get("topic") or "").strip()
        if topic:
            novice_args_by_topic[topic] = arg

    # 3. 遍歷 pro arguments，content 欄位換成 novice，結構欄位保留 pro
    new_arguments = []
    for pro_arg in (pro_result.get("arguments") or []):
        topic = (pro_arg.get("topic") or "").strip()
        na = novice_args_by_topic.get(topic, {})

        merged = {
            # 結構欄位：來自 pro
            "topic": topic,
            "evidence_timestamps": pro_arg.get("evidence_timestamps") or [],
            # 內容欄位：優先 novice，fallback pro
            "position": na.get("position") or pro_arg.get("position", ""),
            "summary": na.get("summary") or pro_arg.get("summary", ""),
            "key_data": (
                na["key_data"]
                if na.get("key_data") is not None
                else pro_arg.get("key_data", [])
            ),
            "related_concepts": (
                na["related_concepts"]
                if na.get("related_concepts") is not None
                else pro_arg.get("related_concepts", [])
            ),
        }
        new_arguments.append(merged)

    novice["arguments"] = new_arguments
    return novice
