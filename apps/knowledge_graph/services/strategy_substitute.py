# apps/knowledge_graph/services/strategy_substitute.py
"""
Substitute 策略：找 seed 的替代標的

Substitution 關係本質上是對稱的（A 替代 B ~ B 替代 A），用無向圖聚合即可，
不需要跑 PPR，直接取 seed 的 1-hop 鄰居，依 mention_count（edge weight）排序。
"""
from __future__ import annotations

from datetime import date

from .graph_window import build_weighted_graph, fetch_links_in_window


def substitute_candidates(seed: str, end_date: date, window_days: int) -> list[dict]:
    links = fetch_links_in_window(end_date, window_days, relation_type="Substitution")
    if not links:
        return []

    G, edge_meta = build_weighted_graph(links, directed=False)
    names = G.vs["name"]
    if seed not in names:
        return []

    seed_idx = names.index(seed)
    candidates = []
    for nb_idx in G.neighbors(seed_idx):
        name = names[nb_idx]
        key = tuple(sorted([seed, name]))
        meta = edge_meta.get(key, {"weight": 0, "reasons": [], "podcast_sources": set()})
        candidates.append({
            "node": name,
            "score": meta["weight"],
            "hops": 1,
            "path": [seed, name],
            "path_reasons": [{"from": seed, "to": name, "reasons": meta["reasons"]}],
            "mention_count": meta["weight"],
            "source_diversity": len(meta["podcast_sources"]),
            "reasons": meta["reasons"],
        })

    candidates.sort(key=lambda c: -c["score"])
    return candidates
