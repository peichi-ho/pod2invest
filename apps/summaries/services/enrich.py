# apps/summaries/services/enrich.py
import json
from pathlib import Path
from typing import Optional

from google import genai

from .gemini import gemini_generate_with_retry, sanitize_json_text, repair_to_valid_json
from .prompts import build_system_instruction


def enrich_arguments_if_empty(
    client: genai.Client,
    model: str,
    mode: str,
    summary: dict,
    inline_text: str,
    raw_save_path: Optional[Path] = None,
) -> dict:
    """
    如果 arguments 大量空白，就要求模型只補 arguments。
    """
    def append_raw(title: str, content: str):
        if not raw_save_path:
            return
        with open(raw_save_path, "a", encoding="utf-8") as f:
            f.write(f"{title}\n{content}\n\n")

    args = summary.get("arguments") or []
    if not isinstance(args, list) or not args:
        return summary

    def is_empty_arg(a: dict) -> bool:
        pos = (a.get("position") or "").strip()
        summ = (a.get("summary") or "").strip()
        kd = a.get("key_data") or []
        ts = a.get("evidence_timestamps") or []
        return (not pos) and (not summ) and (len(kd) == 0) and (len(ts) == 0)

    empty_count = sum(1 for a in args if isinstance(a, dict) and is_empty_arg(a))
    if empty_count < max(1, len(args) // 2):
        return summary

    head = inline_text[:20000]
    tail = inline_text[-10000:] if len(inline_text) > 10000 else ""
    excerpt = f"{head}\n\n---\n（中略）\n---\n\n{tail}" if tail else head

    topics = [a.get("topic", "") for a in args if isinstance(a, dict) and (a.get("topic") or "").strip()]
    system_prompt = build_system_instruction(mode)

    prompt = (
        "===SYSTEM INSTRUCTIONS===\n"
        f"{system_prompt}\n"
        "===END SYSTEM INSTRUCTIONS===\n\n"
        "你現在只要做一件事：『把 arguments 補完整』。\n"
        "硬性規則：\n"
        "- 只能輸出 JSON，不要 ```json，不要解釋\n"
        "- 你要輸出物件：{ \"arguments\": [...] }\n"
        "- arguments 每個主題都必須填：topic, position, summary, key_data, related_concepts, evidence_timestamps\n"
        "- position：一句話立場（不可空）\n"
        "- summary：至少120字、最多220字的主題式摘要（不可空）\n"
        "- key_data：若有明確數字/百分比/日期/指數就放入（沒有可空陣列）\n"
        "- evidence_timestamps：每個主題至少 1 個（從逐字稿（m:ss）挑最相關）\n\n"
        f"===要補的 topics（請維持這些 topic 名稱）===\n{json.dumps(topics, ensure_ascii=False)}\n\n"
        f"===逐字稿節選（前段+後段）===\n{excerpt}\n"
    )

    resp = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=prompt,
        temperature=0.1,
        max_output_tokens=6000,
        max_tries=6,
    )

    t = (getattr(resp, "text", "") or "")
    append_raw("===ARGUMENTS ENRICH RAW===", t)

    clean = sanitize_json_text(t)
    if not clean.strip():
        return summary

    try:
        obj = json.loads(clean)
    except Exception:
        try:
            obj = repair_to_valid_json(client, model, clean)
        except Exception:
            return summary

    new_args = obj.get("arguments")
    if isinstance(new_args, list) and new_args:
        summary["arguments"] = new_args

    return summary


def ensure_min_arguments(
    client: genai.Client,
    model: str,
    mode: str,
    summary: dict,
    inline_text: str,
    raw_save_path: Optional[Path] = None,
    min_topics: int = 5,
) -> dict:
    """
    若 arguments 少於 min_topics，要求模型補到至少 min_topics。
    """
    def append_raw(title: str, content: str):
        if not raw_save_path:
            return
        with open(raw_save_path, "a", encoding="utf-8") as f:
            f.write(f"{title}\n{content}\n\n")

    args = summary.get("arguments") or []
    if isinstance(args, list) and len(args) >= min_topics:
        return summary

    system_prompt = build_system_instruction(mode)

    head = inline_text[:12000]
    tail = inline_text[-3000:] if len(inline_text) > 15000 else ""
    excerpt = head + ("\n\n---\n（中略）\n---\n\n" + tail if tail else "")

    prompt = (
        "===SYSTEM INSTRUCTIONS===\n"
        f"{system_prompt}\n"
        "===END SYSTEM INSTRUCTIONS===\n\n"
        f"你要把 arguments 補到至少 {min_topics} 個主題。\n"
        "只輸出 JSON：{{ \"arguments\": [...] }}，不要解釋 ```。\n"
        "補充時請依以下優先順序：\n"
        "1. 先檢查固定結構型主題是否已存在，若無且逐字稿有相關內容就補上：\n"
        "   「總體經濟環境」「操作策略與建議」「風險提示」\n"
        "2. 再補逐字稿中被認真討論（3句以上）但尚未列出的個股（命名為「個股：XXX」）\n"
        "   或產業主題（命名為「產業：XXX」，例如「產業：記憶體漲價」「產業：PCB族群」）\n"
        "topic 只能用上述這幾種既有格式命名，不可自創其他分類名稱；\n"
        "跟投資／市場／公司／總體經濟完全無關的內容（主持人個人生活、興趣嗜好、閒聊八卦等）不要補成 argument。\n"
        "每個 argument 必填：topic, position（有明確方向）, summary（120–200字）, "
        "key_data, related_concepts, evidence_timestamps（至少1個）。\n\n"
        f"===現有 topics===\n{json.dumps([a.get('topic','') for a in (args or []) if isinstance(a, dict)], ensure_ascii=False)}\n\n"
        f"===逐字稿節選===\n{excerpt}\n"
    )

    resp = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=prompt,
        temperature=0.1,
        max_output_tokens=6000,
        max_tries=6,
    )
    t = (getattr(resp, "text", "") or "")
    append_raw("===ENSURE_MIN_ARGUMENTS RAW===", t)

    clean = sanitize_json_text(t)
    if not clean.strip():
        return summary

    try:
        obj = json.loads(clean)
    except Exception:
        try:
            obj = repair_to_valid_json(client, model, clean)
        except Exception:
            return summary

    new_args = obj.get("arguments")
    if isinstance(new_args, list) and len(new_args) >= min_topics:
        summary["arguments"] = new_args

    return summary