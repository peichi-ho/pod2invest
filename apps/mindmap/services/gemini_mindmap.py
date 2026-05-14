import json
import os
import re
import time
from typing import Any, Dict, List

import requests

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GEMINI_MODEL = "gemini-2.5-flash-lite"
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

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        import re as _re
        fixed = match.group(0)
        fixed = _re.sub(r",\s*([}\]])", r"\1", fixed)
        return json.loads(fixed)


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

1. arguments 內的 summary 不得為長段落
2. summary 若超過 20 字，必須拆成多個短句
3. key_data 若資訊過多，保留最關鍵 3-4 筆
4. 每筆 key_data 亦須 ≤ 20 字

【語言要求】

使用繁體中文
語氣客觀
避免評論語氣

【輸出格式】

請只輸出以下 JSON 結構，不得添加任何說明文字：

{{
  "title": ".",
  "one_sentence_summary": ".",
  "investment_takeaways": {{
    "bullish": ["."],
    "bearish": ["."],
    "watchlist": ["."],
    "podcaster_stance": "."
  }},
  "arguments": [
    {{
      "topic": ".",
      "position": ".",
      "summary": [".", "."],
      "key_data": [
        {{"label": ".", "value": ".", "context": "."}}
      ]
    }}
  ]
}}

重要：
summary 必須為字串陣列
每一個元素為一個短節點
不得為單一長段落字串

請開始轉換以下資料：
{json.dumps(source, ensure_ascii=False)}
""".strip()


def gemini_generate_content(
    prompt: str,
    api_key: str,
    model: str = GEMINI_MODEL,
) -> str:
    # v1beta 支援較新模型（gemini-2.5-flash-lite 等）
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    params = {"key": api_key}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    # 503 時最多重試 3 次
    for attempt in range(3):
        response = requests.post(url, params=params, json=payload, timeout=90)
        if response.status_code == 503:
            wait = (attempt + 1) * 15
            time.sleep(wait)
            continue
        break

    if response.status_code != 200:
        raise ValueError(f"Gemini API 請求失敗（{response.status_code}）：{response.text}")

    data = response.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise ValueError(f"Gemini 回傳格式異常：{json.dumps(data, ensure_ascii=False)}")


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


def generate_mindmap_json(summary_data: Dict[str, Any], api_key: str = "") -> Dict[str, Any]:
    if not api_key:
        api_key = os.environ.get(GEMINI_API_KEY_ENV, "").strip()

    vertex_project = os.environ.get("VERTEX_PROJECT_ID", "").strip()
    if not api_key and not vertex_project:
        raise ValueError(f"找不到環境變數 {GEMINI_API_KEY_ENV}")

    if not isinstance(summary_data, dict):
        raise ValueError("summary_data 必須是 JSON 物件。")

    fallback_title = _clean_text(summary_data.get("title")) or "Mindmap"
    prompt = build_prompt_for_mindmap(summary_data)

    if api_key:
        # 原本的 REST 路徑（AI Studio API key）
        gemini_text = gemini_generate_content(prompt=prompt, api_key=api_key, model=GEMINI_MODEL)
    else:
        # Vertex AI 路徑：使用共用 SDK client
        from apps.summaries.services.gemini import make_client, gemini_generate_with_retry
        client = make_client()
        resp = gemini_generate_with_retry(
            client=client,
            model=f"models/{GEMINI_MODEL}",
            prompt_text=prompt,
            temperature=0.2,
            max_output_tokens=4096,
            max_tries=3,
        )
        gemini_text = getattr(resp, "text", "") or ""

    raw_json = _extract_json(gemini_text)
    return normalize_mindmap_json(raw_json, fallback_title=fallback_title)
