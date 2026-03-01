# apps/summaries/services/engine.py
from pathlib import Path
from typing import Optional

from .srt import parse_srt, srt_to_inline_text
from .gemini import make_client
from .chunking import summarize_with_optional_chunking
from .harmonize import harmonize_tags_entities, harmonize_investment_takeaways_dual_style

def summarize_from_srt_text(
    *,
    api_key: str,
    srt_text: str,
    mode: str,  # "novice" | "pro" | "both"
    model: str = "models/gemini-2.5-flash-lite",
    chunk_threshold_chars: int = 30000,
    debug_chars: int = 0,
    raw_save_path: Optional[Path] = None,
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
        novice = summarize_with_optional_chunking(
            client=client,
            model=model,
            mode="novice",
            inline_text=inline_text,
            raw_save_path=raw_save_path,
            chunk_threshold_chars=chunk_threshold_chars,
        )
        pro = summarize_with_optional_chunking(
            client=client,
            model=model,
            mode="pro",
            inline_text=inline_text,
            raw_save_path=raw_save_path,
            chunk_threshold_chars=chunk_threshold_chars,
        )

        novice, pro = harmonize_tags_entities(novice, pro)
        novice, pro = harmonize_investment_takeaways_dual_style(novice, pro)
        return {"novice": novice, "pro": pro}

    return summarize_with_optional_chunking(
        client=client,
        model=model,
        mode=mode,
        inline_text=inline_text,
        raw_save_path=raw_save_path,
        chunk_threshold_chars=chunk_threshold_chars,
    )