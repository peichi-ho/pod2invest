# apps/summaries/services/harmonize.py
import re
from typing import List

from .tag_taxonomy import (
    empty_classification,
    normalize_classification_dict,
    flatten_classification_to_tags,
    dedupe_str_list,
)


def _dedupe_str_list(xs: List[str]) -> List[str]:
    return dedupe_str_list(xs)


def harmonize_classification_entities(novice: dict, pro: dict) -> tuple[dict, dict]:
    """
    讓 classification / tags / entities 兩版完全一致（用 union 後去重）。
    tags 不再自由生成，而是由 classification 展平得到。
    """
    def get_cls(obj):
        c = obj.get("classification") or {}
        c = normalize_classification_dict(c)
        return {
            "financial_topics": list(c.get("financial_topics") or []),
            "industries": list(c.get("industries") or []),
            "content_types": list(c.get("content_types") or []),
            "markets": list(c.get("markets") or []),
            "asset_types": list(c.get("asset_types") or []),
        }

    nc = get_cls(novice)
    pc = get_cls(pro)

    merged_cls = {
        "financial_topics": _dedupe_str_list(nc["financial_topics"] + pc["financial_topics"]),
        "industries": _dedupe_str_list(nc["industries"] + pc["industries"]),
        "content_types": _dedupe_str_list(nc["content_types"] + pc["content_types"]),
        "markets": _dedupe_str_list(nc["markets"] + pc["markets"]),
        "asset_types": _dedupe_str_list(nc["asset_types"] + pc["asset_types"]),
    }
    merged_cls = normalize_classification_dict(merged_cls)
    merged_tags = flatten_classification_to_tags(merged_cls)

    def get_ents(obj):
        e = obj.get("entities") or {}
        return {
            "companies_or_stocks": list(e.get("companies_or_stocks") or []),
            "countries_or_regions": list(e.get("countries_or_regions") or []),
            "people": list(e.get("people") or []),
        }

    ne = get_ents(novice)
    pe = get_ents(pro)

    ents = {
        "companies_or_stocks": _dedupe_str_list(ne["companies_or_stocks"] + pe["companies_or_stocks"]),
        "countries_or_regions": _dedupe_str_list(ne["countries_or_regions"] + pe["countries_or_regions"]),
        "people": _dedupe_str_list(ne["people"] + pe["people"]),
    }

    novice["classification"] = merged_cls
    pro["classification"] = merged_cls
    novice["tags"] = list(merged_tags)
    pro["tags"] = list(merged_tags)
    novice["entities"] = ents
    pro["entities"] = ents
    return novice, pro


def harmonize_investment_takeaways_dual_style(novice: dict, pro: dict) -> tuple[dict, dict]:
    """
    只處理 1-1：確保兩邊三個區塊都有內容（不要一邊有一邊沒有）
    同時保留「不同解讀」：缺項時用「轉寫」補，而不是直接複製同一句。
    """
    def get_list(obj, key):
        inv = obj.get("investment_takeaways") or {}
        v = inv.get(key) or []
        return v if isinstance(v, list) else []

    def set_list(obj, key, values):
        obj.setdefault("investment_takeaways", {})
        obj["investment_takeaways"][key] = list(values)

    def noviceify(s):
        if isinstance(s, dict):
            return s
        s = (s or "").strip()
        if not s:
            return s
        if s.startswith("關注：") or s.startswith("觀察："):
            s = s.replace("關注：", "").replace("觀察：", "").strip()
        return f"{s}"

    def proify(s):
        if isinstance(s, dict):
            return s
        s = (s or "").strip()
        if not s:
            return s
        s = re.sub(r"（.*?）", "", s).strip()
        if not s.startswith("關注："):
            s = f"關注：{s}"
        return s

    for k in ("bullish", "bearish", "watchlist"):
        nl = get_list(novice, k)
        pl = get_list(pro, k)

        if not nl and not pl:
            continue

        target_len = max(len(nl), len(pl), 1)

        nn = list(nl)
        while len(nn) < target_len:
            src = pl[len(nn)] if len(pl) > len(nn) else (pl[-1] if pl else "")
            nn.append(noviceify(src))

        pp = list(pl)
        while len(pp) < target_len:
            src = nl[len(pp)] if len(nl) > len(pp) else (nl[-1] if nl else "")
            pp.append(proify(src))

        nn = [noviceify(x) for x in nn]
        pp = [proify(x) for x in pp]

        set_list(novice, k, nn)
        set_list(pro, k, pp)

    n_inv = novice.get("investment_takeaways") or {}
    p_inv = pro.get("investment_takeaways") or {}
    if not (n_inv.get("podcaster_stance") or "").strip() and (p_inv.get("podcaster_stance") or "").strip():
        novice["investment_takeaways"]["podcaster_stance"] = p_inv["podcaster_stance"]
    if not (p_inv.get("podcaster_stance") or "").strip() and (n_inv.get("podcaster_stance") or "").strip():
        pro["investment_takeaways"]["podcaster_stance"] = n_inv["podcaster_stance"]

    return novice, pro
