# apps/summaries/services/gemini.py
import json
import random
import re
import time
from typing import Optional

from google import genai

from django.conf import settings


def make_client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=settings.VERTEX_PROJECT_ID,
        location=settings.VERTEX_LOCATION,
    )


def gemini_generate_with_retry(
    client: genai.Client,
    model: str,
    prompt_text: str,
    temperature: float,
    max_output_tokens: int,
    max_tries: int = 6,
):
    base = 1.0
    last_err: Optional[Exception] = None

    for attempt in range(1, max_tries + 1):
        try:
            return client.models.generate_content(
                model=model,
                contents=[{"role": "user", "parts": [{"text": prompt_text}]}],
                config={
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                },
            )
        except Exception as e:
            last_err = e
            msg = str(e)
            is_503 = ("503" in msg) or ("UNAVAILABLE" in msg) or ("overloaded" in msg)
            is_429 = ("429" in msg) or ("RESOURCE_EXHAUSTED" in msg) or ("quota" in msg.lower())

            if not (is_503 or is_429) or attempt == max_tries:
                raise

            sleep_s = min(70.0, base * (2 ** (attempt - 1)) + random.uniform(0, 0.5))
            time.sleep(sleep_s)

    raise last_err


def sanitize_json_text(text: str) -> str:
    if not text:
        return ""

    s = text.lstrip("\ufeff")

    s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.IGNORECASE | re.MULTILINE)
    s = re.sub(r"\s*```\s*$", "", s, flags=re.MULTILINE)

    first = s.find("{")
    if first != -1:
        s = s[first:]
    else:
        return ""

    return s.strip()


def extract_json_object(text: str) -> Optional[str]:
    if not text:
        return None

    s = text.lstrip("\ufeff").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s).strip()

    start = s.find("{")
    if start == -1:
        return None

    s = s[start:]

    depth = 0
    in_str = False
    esc = False

    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[: i + 1]

    return None


def repair_to_valid_json(client: genai.Client, model: str, bad_text: str) -> dict:
    prompt = (
        "你是一個 JSON 修復器。\n"
        "請把下面的內容轉成『合法 JSON』：\n"
        "- 只能輸出 JSON（不要 markdown，不要解釋）\n"
        "- 必須符合標準 JSON 規格（雙引號、逗號、不可有 trailing comma）\n"
        "- 不要改動欄位結構與語意，只修復語法\n\n"
        "===待修復內容開始===\n"
        f"{bad_text}\n"
        "===待修復內容結束===\n"
    )

    resp = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=prompt,
        temperature=0.0,
        max_output_tokens=8000,
        max_tries=6,
    )

    fixed = (getattr(resp, "text", "") or "")
    clean = sanitize_json_text(fixed)

    if not clean.strip():
        raise RuntimeError("JSON repair returned empty/invalid text.")

    # 先試直接 parse
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # 再試抽第一個完整 JSON 物件
    js = extract_json_object(clean)
    if js:
        try:
            return json.loads(js)
        except json.JSONDecodeError:
            pass

    # 最後再試一次：如果 bad_text 本身其實已經有完整 JSON，就直接抽
    original_clean = sanitize_json_text(bad_text)
    js2 = extract_json_object(original_clean)
    if js2:
        try:
            return json.loads(js2)
        except json.JSONDecodeError:
            pass

    preview = clean[:400].replace("\n", "\\n")
    raise RuntimeError(f"JSON repair still invalid. Preview: {preview}")