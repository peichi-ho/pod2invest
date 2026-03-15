"""
annotator.py

功能：
- 從專有名詞資料庫（glossary_db）讀取所有已定義的名詞
- 對輸入的摘要文字進行字串比對
- 找出摘要中需要補充說明的專有名詞
- 回傳每個名詞在摘要中的位置與對應解釋

設計重點：
- 採用精確字串比對（非 AI / 非 NLP）
- 以「長詞優先」策略避免名詞重疊誤判
- 不修改原始摘要文字，只回傳標註資訊

角色定位：
- 系統的「核心邏輯層（Core Logic）」
"""

from __future__ import annotations
from dataclasses import dataclass
import re
from typing import List

from apps.glossary.models import GlossaryTerm


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    term_id: int
    surface: str
    canonical: str
    short_definition: str


def annotate(text: str, max_matches: int = 100):
    text = text or ""
    if not text:
        return {"text": text, "matches": []}

    terms = GlossaryTerm.objects.filter(is_active=True).prefetch_related("aliases")

    # 1) 展開所有可匹配字面（term + aliases）
    surfaces = []
    for t in terms:
        candidates = [t.term] + [a.alias for a in t.aliases.all()]
        seen = set()
        for c in candidates:
            c = (c or "").strip()
            if not c:
                continue
            key = c.lower()
            if key in seen:
                continue
            seen.add(key)
            surfaces.append((c, t))

    # 2) 長詞優先
    surfaces.sort(key=lambda x: len(x[0]), reverse=True)

    occupied = [False] * (len(text) + 1)
    matches: List[Match] = []

    for surface, t in surfaces:
        pat = re.compile(re.escape(surface), flags=re.IGNORECASE)
        for m in pat.finditer(text):
            s, e = m.start(), m.end()
            if s == e:
                continue
            if any(occupied[s:e]):
                continue

            matches.append(
                Match(
                    start=s,
                    end=e,
                    term_id=t.id,
                    surface=text[s:e],
                    canonical=t.term,
                    short_definition=t.short_definition,
                )
            )
            for i in range(s, e):
                occupied[i] = True

            if len(matches) >= max_matches:
                break
        if len(matches) >= max_matches:
            break

    matches.sort(key=lambda x: x.start)

    return {
        "text":
            text,
        "matches": [{
            "start": m.start,
            "end": m.end,
            "term_id": m.term_id,
            "surface": m.surface,
            "canonical": m.canonical,
            "short_definition": m.short_definition,
        } for m in matches],
    }
