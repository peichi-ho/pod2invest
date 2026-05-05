# apps/summaries/services/engine.py
from pathlib import Path
from typing import Optional

from .srt import parse_srt, srt_to_inline_text
from .gemini import make_client
from .chunking import summarize_with_optional_chunking
from .harmonize import harmonize_classification_entities, harmonize_investment_takeaways_dual_style


def summarize_from_srt_text(
    *,
    srt_text: str,
    mode: str,  # "novice" | "pro" | "both"
    model: str = "gemini-2.5-flash-lite",
    chunk_threshold_chars: int = 20000,
    debug_chars: int = 0,
    raw_save_path: Optional[Path] = None,
):
    """
    Django/DRF 會呼叫這個函式。不要做檔案 I/O（raw_save_path 例外：你想記錄 debug raw 才用）。
    回傳：
      - mode=novice/pro -> dict summary
      - mode=both       -> {"novice":..., "pro":...}
    """
    client = make_client()

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

        try:
            novice = summarize_with_optional_chunking(
                client=client,
                model=model,
                mode="novice",
                inline_text=inline_text,
                raw_save_path=novice_raw,
                chunk_threshold_chars=chunk_threshold_chars,
            )
        except Exception as e:
            errors["novice"] = str(e)

        try:
            pro = summarize_with_optional_chunking(
                client=client,
                model=model,
                mode="pro",
                inline_text=inline_text,
                raw_save_path=pro_raw,
                chunk_threshold_chars=chunk_threshold_chars,
            )
        except Exception as e:
            errors["pro"] = str(e)

        if novice and pro:
            novice, pro = harmonize_classification_entities(novice, pro)
            novice, pro = harmonize_investment_takeaways_dual_style(novice, pro)
            return {"novice": novice, "pro": pro}

        if novice:
            return {"novice": novice, "pro": None, "errors": errors}

        if pro:
            return {"novice": None, "pro": pro, "errors": errors}

        raise RuntimeError(f"both mode failed: {errors}")

    return summarize_with_optional_chunking(
        client=client,
        model=model,
        mode=mode,
        inline_text=inline_text,
        raw_save_path=raw_save_path,
        chunk_threshold_chars=chunk_threshold_chars,
    )
