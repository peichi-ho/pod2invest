import json
import re
from pathlib import Path
from typing import Optional, Any

from google import genai

from .gemini import gemini_generate_with_retry, sanitize_json_text, repair_to_valid_json


def _append_raw(raw_save_path: Optional[Path], title: str, content: str) -> None:
    if not raw_save_path:
        return
    with open(raw_save_path, "a", encoding="utf-8") as f:
        f.write(f"{title}\n{content}\n\n")


def _normalize_text(s: Any) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    s = s.replace("　", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def _normalize_key_text(s: Any) -> str:
    return _normalize_text(s).lower().replace(" ", "")


def _clean_quote(s: Any) -> str:
    import re as _re
    s = _normalize_text(s)
    s = _re.sub(r"（\d+:\d{2}）", "", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s


def _safe_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _normalize_timeframe(tf: Any) -> Optional[str]:
    tf = _normalize_text(tf)
    if not tf:
        return None

    tf_key = _normalize_key_text(tf)

    if "未來兩年" in tf:
        return "未來兩年"
    if "2026" in tf_key:
        return "2026"
    if "2025" in tf_key:
        return "2025"
    if "短中期" in tf:
        return "短中期"
    if "未來幾年" in tf:
        return "未來幾年"
    if "長期" in tf:
        return "長期"
    if "十年" in tf or "10年" in tf:
        return "長期"

    return tf


def _clean_reviewed_call(call: dict) -> Optional[dict]:
    if not isinstance(call, dict):
        return None

    asset = _normalize_text(call.get("asset"))
    direction = _normalize_text(call.get("direction")).lower()
    thesis = _normalize_text(call.get("thesis"))
    timeframe = _normalize_timeframe(call.get("timeframe"))
    evidence_quote = _clean_quote(call.get("evidence_quote"))
    summary_label = _normalize_text(call.get("summary_label"))

    evidence_timestamps = _safe_list(call.get("evidence_timestamps"))
    evidence_timestamps = [_normalize_text(x) for x in evidence_timestamps if _normalize_text(x)]

    if not asset:
        return None
    if direction not in {"bullish", "bearish"}:
        return None
    if not thesis:
        return None
    if not evidence_quote:
        return None

    summary_label = _normalize_text(call.get("summary_label"))
    evidence_quote = _clean_quote(call.get("evidence_quote"))

    return {
        "asset": asset,
        "direction": direction,
        "timeframe": timeframe,
        "thesis": thesis,
        "summary_label": summary_label,
        "evidence_timestamps": evidence_timestamps,
        "evidence_quote": evidence_quote,
    }


def _dedupe_strings(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for x in items or []:
        xx = _normalize_text(x)
        if not xx:
            continue
        key = _normalize_key_text(xx)
        if key in seen:
            continue
        seen.add(key)
        out.append(xx)
    return out


def _clamp_timestamps(calls: list[dict], max_per_call: int = 4) -> list[dict]:
    out = []
    for c in calls:
        cc = dict(c)
        cc["evidence_timestamps"] = _dedupe_strings(_safe_list(cc.get("evidence_timestamps")))[:max_per_call]
        out.append(cc)
    return out


def _sort_calls(calls: list[dict]) -> list[dict]:
    def sort_key(x: dict):
        asset = _normalize_text(x.get("asset"))
        direction = _normalize_text(x.get("direction"))
        timeframe = _normalize_text(x.get("timeframe") or "")
        ts = _safe_list(x.get("evidence_timestamps"))
        first_ts = _normalize_text(ts[0]) if ts else ""
        return (asset, direction, timeframe, first_ts)

    return sorted(calls, key=sort_key)


def consolidate_outlook_payload(
    client: genai.Client,
    model: str,
    reviewed_calls: list[dict],
    raw_save_path: Optional[Path] = None,
) -> dict:
    cleaned_reviewed = []
    for c in reviewed_calls or []:
        cc = _clean_reviewed_call(c)
        if cc is not None:
            cleaned_reviewed.append(cc)

    prompt = (
        "你是一位投資 thesis 整理編輯。\n"
        "你的任務是把已審核通過的 reviewed_calls 整理成最終精煉的 outlook_calls。\n\n"

        "【你的任務】\n"
        "1. 只根據 reviewed_calls 做整合，不要重新看逐字稿。\n"
        "2. 同一股票若只是同一 thesis 的重複表達，請合併。\n"
        "3. 同一股票若存在不同方向或不同時間框架，可保留多筆。\n"
        "4. 最終每檔股票盡量保留 1–3 筆最具代表性的投資 thesis。\n\n"

        "【合併原則】\n"
        "應合併的情況：\n"
        "- 同一股票\n"
        "- 同一方向\n"
        "- 同一時間框架\n"
        "- 本質在講同一個核心 thesis，只是支撐細節不同\n\n"

        "不應合併的情況：\n"
        "- 同一股票但方向不同\n"
        "- 同一股票但明顯是不同時間框架\n"
        "- 同一股票但核心 thesis 不同\n\n"

        "【輸出要求】\n"
        "1. thesis 要保留完整投資判斷\n"
        "2. evidence_quote 要保留最能代表該 thesis 的句子\n"
        "3. evidence_timestamps 保留 1–4 個代表時間點即可\n"
        "4. 不要輸出 rejected_calls\n\n"

        "【輸出格式】\n"
        "只能輸出合法 JSON：\n"
        "{\n"
        '  "valid_calls": [\n'
        "    {\n"
        '      "asset": "標的名稱（公司/指數/商品）",\n'
        '      "direction": "bullish 或 bearish",\n'
        '      "timeframe": "null / 2025 / 2026 / 短中期 / 未來幾年 / 未來兩年 / 長期",\n'
        '      "thesis": "精煉後的一句完整投資判斷",\n'
        '      "summary_label": "一句讓使用者第一眼看懂的預測摘要，含標的+方向+理由/時間，若 reviewed_calls 已有則直接沿用，否則自行生成",\n'
        '      "evidence_timestamps": ["m:ss"],\n'
        '      "evidence_quote": "最能代表該 thesis 的說話者原話，最多 60 字"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"

        "======== reviewed_calls ========\n"
        f"{json.dumps(cleaned_reviewed, ensure_ascii=False, indent=2)}\n"
    )

    resp = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=prompt,
        temperature=0.0,
        max_output_tokens=4000,
        max_tries=6,
    )

    text = (getattr(resp, "text", "") or "")
    _append_raw(raw_save_path, "===OUTLOOK CONSOLIDATE RAW===", text)

    clean = sanitize_json_text(text)
    if not clean.strip():
        return {"valid_calls": []}

    try:
        obj = json.loads(clean)
    except json.JSONDecodeError:
        try:
            obj = repair_to_valid_json(client, model, clean)
        except Exception:
            return {"valid_calls": []}

    if not isinstance(obj, dict):
        return {"valid_calls": []}

    valid_calls = obj.get("valid_calls", []) or []
    if not isinstance(valid_calls, list):
        valid_calls = []

    cleaned_valid = []
    for c in valid_calls:
        cc = _clean_reviewed_call(c)
        if cc is not None:
            cleaned_valid.append(cc)

    cleaned_valid = _clamp_timestamps(cleaned_valid, max_per_call=4)
    cleaned_valid = _sort_calls(cleaned_valid)

    payload = {"valid_calls": cleaned_valid}

    _append_raw(
        raw_save_path,
        "===OUTLOOK CONSOLIDATE CLEANED===",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )

    return payload


def consolidate_outlook_calls(
    client: genai.Client,
    model: str,
    reviewed_calls: list[dict],
    raw_save_path: Optional[Path] = None,
) -> list[dict]:
    payload = consolidate_outlook_payload(
        client=client,
        model=model,
        reviewed_calls=reviewed_calls,
        raw_save_path=raw_save_path,
    )
    return payload.get("valid_calls", [])
