# apps/knowledge_graph/services/strategy_coimpact.py
"""
Co-impact 策略：找 seed 所在的「共同受影響」社群

1. 用 Co-impact 邊建圖，跑 Leiden 分群（重用 leiden_cluster.run_leiden）
2. 找出 seed 所在的社群
3. 在該社群的子圖上，以 seed 為起點重跑 PPR，重新排序社群成員
   （社群本身是全域分群結果，跟 seed 距離無關；PPR 再排序才能反映
    「哪些成員跟 seed 關聯最緊密」，不論社群大小都套用，維持跟其他
    策略一致的排序邏輯）
"""
from __future__ import annotations

from datetime import date

from .graph_window import build_weighted_graph, fetch_links_in_window
from .leiden_cluster import run_leiden
from .ppr_utils import run_ppr


def co_impact_candidates(
    seed: str,
    end_date: date,
    window_days: int,
    resolution: float = 1.0,
    min_community_size: int = 3,
) -> list[dict]:
    links = fetch_links_in_window(end_date, window_days, relation_type="Co-impact")
    if not links:
        return []

    leiden_result = run_leiden(links, resolution=resolution, min_community_size=min_community_size)

    seed_community = next(
        (c for c in leiden_result["communities"] if seed in c["companies"]),
        None,
    )
    if seed_community is None:
        return []

    community_members = set(seed_community["companies"])
    community_links = [
        lk for lk in links
        if lk["source"] in community_members and lk["target"] in community_members
    ]

    G, edge_meta = build_weighted_graph(community_links, directed=False)
    return run_ppr(G, seed, edge_meta=edge_meta, damping=0.85)
