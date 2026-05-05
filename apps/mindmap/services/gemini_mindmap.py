import json
import re
from typing import Any, Dict, List

from google import genai
from django.conf import settings

GEMINI_MODEL = settings.GEMINI_MODEL

MAX_ARGUMENTS = 8


def _clean_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value.replace("\n", " ").strip()

    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            text = _clean_text(item)
            if text:
                parts.append(text)
        return " ".join(parts).strip()

    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False).replace("\n", " ").strip()

    return str(value).replace("\n", " ").strip()


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


def _extract_json(text: str) -> Dict[str, Any]:
    raw = _strip_code_fences(text)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        raise ValueError("Gemini 回傳內容不包含有效 JSON。")

    return json.loads(match.group(0))


def build_prompt_for_mindmap(data: Dict[str, Any]) -> str:
    title_hint = data.get("title") or data.get("title_hint") or ""
    one_sentence = data.get("one_sentence_summary") or ""
    investment_takeaways = data.get("investment_takeaways", {}) or {}
    arguments = data.get("arguments", []) or []

    compact_args: List[Dict[str, Any]] = []
    for argument in arguments[:MAX_ARGUMENTS]:
        compact_args.append({
            "topic": argument.get("topic", ""),
            "position": argument.get("position", ""),
            "summary": argument.get("summary", ""),
            "key_data": argument.get("key_data", []) or [],
        })

    source = {
        "title_hint": title_hint,
        "one_sentence_summary": one_sentence,
        "investment_takeaways": investment_takeaways,
        "arguments": compact_args,
    }

    return f"""
你是一個專門將 Podcast 摘要轉換為「高壓縮心智圖節點」的結構化編輯器。

目標：
將原始摘要完整保留所有核心論點
但將內容高度濃縮為適合心智圖閱讀的短節點

【最重要原則】

1. 不得刪減任何原始核心論點
2. 原本有幾個論點，必須保留相同數量
3. 只能濃縮，不可刪除

【節點壓縮規則】

1. 每一個項目最多 20 個中文字
2. 如果超過 20 字，必須拆成多個平行節點
3. 每個節點只講一件事
4. 若一段包含兩件以上的事，必須拆開
5. 可以使用標點符號
6. 允許短動詞句，但不可出現長複合句
7. 禁止出現說明型段落
8. 禁止冗詞，例如：可能、也許、其實
9. 禁止產生新的觀點
10. 禁止刪除重要資訊

【摘要處理規則】

1. arguments 最多保留 5 個，選最重要的
2. 每個 argument 的 summary 最多 3 個元素，不可更多
3. 每個 summary 元素 ≤ 15 個中文字，只講一件事
4. key_data 完全不需要輸出，從 JSON 格式中移除
5. position 完全不需要輸出，從 JSON 格式中移除

【語言要求】

使用繁體中文
語氣客觀
避免評論語氣

【輸出格式】

請只輸出以下 JSON 結構，不得添加任何說明文字：

{{
  "title": ".",
  "arguments": [
    {{
      "topic": ".",
      "summary": [".", "."]
    }}
  ]
}}

重要：
- summary 每個最多 3 個元素
- 每個元素 ≤ 20 字
- 不需要 investment_takeaways、key_data、position、one_sentence_summary

請開始轉換以下資料：
{json.dumps(source, ensure_ascii=False)}
""".strip()


def gemini_generate_content(
    prompt: str,
    model: str = GEMINI_MODEL,
) -> str:
    client = genai.Client(
        vertexai=True,
        project=settings.VERTEX_PROJECT_ID,
        location=settings.VERTEX_LOCATION,
    )
    response = client.models.generate_content(model=model, contents=prompt)
    return response.text


def normalize_mindmap_json(mm_json: Dict[str, Any], fallback_title: str = "Mindmap") -> Dict[str, Any]:
    if not isinstance(mm_json, dict):
        raise ValueError("心智圖結果必須是 dict。")

    title = _clean_text(mm_json.get("title")) or fallback_title
    one_sentence_summary = _clean_text(mm_json.get("one_sentence_summary"))

    investment_takeaways = mm_json.get("investment_takeaways", {}) or {}
    if not isinstance(investment_takeaways, dict):
        investment_takeaways = {}

    bullish = investment_takeaways.get("bullish", []) or []
    bearish = investment_takeaways.get("bearish", []) or []
    watchlist = investment_takeaways.get("watchlist", []) or []
    podcaster_stance = _clean_text(investment_takeaways.get("podcaster_stance"))

    normalized_arguments = []
    arguments = mm_json.get("arguments", []) or []

    if not isinstance(arguments, list):
        arguments = []

    for index, argument in enumerate(arguments, start=1):
        if not isinstance(argument, dict):
            continue

        topic = _clean_text(argument.get("topic")) or f"論點 {index}"
        position = _clean_text(argument.get("position"))

        summary = argument.get("summary", []) or []
        if isinstance(summary, str):
            summary = [summary]
        if not isinstance(summary, list):
            summary = []

        summary = [_clean_text(item) for item in summary if _clean_text(item)]

        key_data = argument.get("key_data", []) or []
        if not isinstance(key_data, list):
            key_data = []

        normalized_key_data = []
        for row in key_data[:4]:
            if not isinstance(row, dict):
                continue
            normalized_key_data.append({
                "label": _clean_text(row.get("label")),
                "value": _clean_text(row.get("value")),
                "context": _clean_text(row.get("context")),
            })

        normalized_arguments.append({
            "topic": topic,
            "position": position,
            "summary": summary,
            "key_data": normalized_key_data,
        })

    return {
        "title": title,
        "one_sentence_summary": one_sentence_summary,
        "investment_takeaways": {
            "bullish": [_clean_text(item) for item in bullish if _clean_text(item)],
            "bearish": [_clean_text(item) for item in bearish if _clean_text(item)],
            "watchlist": [_clean_text(item) for item in watchlist if _clean_text(item)],
            "podcaster_stance": podcaster_stance,
        },
        "arguments": normalized_arguments,
    }


def generate_mindmap_json(summary_data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(summary_data, dict):
        raise ValueError("summary_data 必須是 JSON 物件。")

    fallback_title = _clean_text(summary_data.get("title")) or "Mindmap"

    prompt = build_prompt_for_mindmap(summary_data)
    print("Calling Gemini (Vertex AI)...")

    gemini_text = gemini_generate_content(prompt=prompt, model=GEMINI_MODEL)
    print("Gemini response received.")

    raw_json = _extract_json(gemini_text)
    return normalize_mindmap_json(raw_json, fallback_title=fallback_title)
