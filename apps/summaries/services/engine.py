# apps/summaries/services/engine.py
from pathlib import Path
from typing import Optional

from .srt import parse_srt, srt_to_inline_text
from .gemini import make_client
from .chunking import summarize_with_optional_chunking, generate_novice_content, _merge_novice_into_pro
from .harmonize import harmonize_classification_entities, harmonize_investment_takeaways_dual_style
from .postprocess import normalize_schema


def summarize_from_srt_text(
    *,
    api_key: str,
    srt_text: str,
    mode: str,  # "novice" | "pro" | "both"
    model: str = "models/gemini-2.5-flash-lite",
    outlook_model: str = "models/gemini-2.5-flash",
    chunk_threshold_chars: int = 20000,
    debug_chars: int = 0,
    raw_save_path: Optional[Path] = None,
    published_at=None,
    forced_topics: list = None,
    pro_result: dict = None,
):
    """
    Django/DRF 會呼叫這個函式。不要做檔案 I/O（raw_save_path 例外：你想記錄 debug raw 才用）。
    回傳：
      - mode=novice/pro -> dict summary
      - mode=both       -> {"novice":..., "pro":...}
    """
    client = make_client(api_key)

    items = parse_srt(srt_text)
    inline_text = srt_to_inline_text(items)

    if debug_chars and debug_chars > 0:
        inline_text = inline_text[:debug_chars]

    if mode == "both":
        novice_raw = None
        pro_raw = None

        if raw_save_path:
            novice_raw = raw_save_path.with_name(raw_save_path.stem + "__novice.txt")
            pro_raw = raw_save_path.with_name(raw_save_path.stem + "__pro.txt")

        errors = {}
        novice = None
        pro = None

        # pro 先跑完整摘要
        try:
            pro = summarize_with_optional_chunking(
                client=client,
                model=model,
                mode="pro",
                inline_text=inline_text,
                raw_save_path=pro_raw,
                chunk_threshold_chars=chunk_threshold_chars,
                outlook_model=outlook_model,
                published_at=published_at,
            )
        except Exception as e:
            errors["pro"] = str(e)

        if pro:
            # novice 只跑 investment_takeaways + arguments，其餘從 pro 複製
            _forced = [arg["topic"] for arg in (pro.get("arguments") or []) if arg.get("topic")]
            try:
                novice_content = generate_novice_content(
                    client=client,
                    model=model,
                    inline_text=inline_text,
                    forced_topics=_forced,
                    pro_takeaways=pro.get("investment_takeaways"),
                    raw_save_path=novice_raw,
                    chunk_threshold_chars=chunk_threshold_chars,
                )
                novice = normalize_schema(_merge_novice_into_pro(pro, novice_content))
            except Exception as e:
                errors["novice"] = str(e)

        if novice and pro:
            return {"novice": novice, "pro": pro}

        if novice:
            return {"novice": novice, "pro": None, "errors": errors}

        if pro:
            return {"novice": None, "pro": pro, "errors": errors}

        raise RuntimeError(f"both mode failed: {errors}")

    # mode="novice" 且有 pro_result 時，走輕量化路徑
    if mode == "novice" and pro_result:
        _forced = [arg["topic"] for arg in (pro_result.get("arguments") or []) if arg.get("topic")]
        if _forced:
            novice_content = generate_novice_content(
                client=client,
                model=model,
                inline_text=inline_text,
                forced_topics=_forced,
                pro_takeaways=pro_result.get("investment_takeaways"),
                raw_save_path=raw_save_path,
                chunk_threshold_chars=chunk_threshold_chars,
            )
            return normalize_schema(_merge_novice_into_pro(pro_result, novice_content))

    return summarize_with_optional_chunking(
        client=client,
        model=model,
        mode=mode,
        inline_text=inline_text,
        raw_save_path=raw_save_path,
        chunk_threshold_chars=chunk_threshold_chars,
        outlook_model=outlook_model,
        published_at=published_at,
        forced_topics=forced_topics,
    )
