# apps/summaries/services/srt.py
import re
from dataclasses import dataclass
from typing import List, Optional

TIME_RE = re.compile(
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})\s*-->\s*"
    r"(?P<h2>\d{2}):(?P<m2>\d{2}):(?P<s2>\d{2}),(?P<ms2>\d{3})"
)

@dataclass
class SrtItem:
    start_ms: int
    end_ms: int
    text: str


def hms_to_ms(h: int, m: int, s: int, ms: int) -> int:
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def ms_to_mss(ms: int) -> str:
    total_sec = ms // 1000
    mm = total_sec // 60
    ss = total_sec % 60
    return f"{mm}:{ss:02d}"


def mss_to_seconds(ts: str) -> Optional[int]:
    """
    Parse 'm:ss' or 'mm:ss' into seconds.
    Return None if invalid.
    """
    ts = (ts or "").strip()
    m = re.fullmatch(r"(\d+):([0-5]\d)", ts)
    if not m:
        return None
    mm = int(m.group(1))
    ss = int(m.group(2))
    return mm * 60 + ss


def seconds_to_mss(sec: int) -> str:
    if sec < 0:
        sec = 0
    mm = sec // 60
    ss = sec % 60
    return f"{mm}:{ss:02d}"


def parse_srt(content: str) -> List[SrtItem]:
    lines = [ln.rstrip("\n") for ln in (content or "").splitlines()]
    items: List[SrtItem] = []
    i, n = 0, len(lines)

    while i < n:
        if not lines[i].strip():
            i += 1
            continue

        # optional index line
        if lines[i].strip().isdigit():
            i += 1
            if i >= n:
                break

        m = TIME_RE.search(lines[i])
        if not m:
            i += 1
            continue

        start_ms = hms_to_ms(int(m["h"]), int(m["m"]), int(m["s"]), int(m["ms"]))
        end_ms = hms_to_ms(int(m["h2"]), int(m["m2"]), int(m["s2"]), int(m["ms2"]))
        i += 1

        text_lines = []
        while i < n and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1

        text = " ".join(text_lines).strip()
        if text:
            items.append(SrtItem(start_ms=start_ms, end_ms=end_ms, text=text))

    return items


def srt_to_inline_text(items: List[SrtItem]) -> str:
    parts = []
    for it in items:
        ts = ms_to_mss(it.start_ms)
        parts.append(f"（{ts}）{it.text}")
    return "".join(parts)