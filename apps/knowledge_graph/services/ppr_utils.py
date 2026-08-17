# apps/knowledge_graph/services/ppr_utils.py
"""
Personalized PageRank 共用工具，供 Supply / Co-impact 策略共用。
"""
from __future__ import annotations


def _edge_reason(edge_meta: dict | None, a: str, b: str) -> dict | None:
    if edge_meta is None:
        return None
    return edge_meta.get((a, b)) or edge_meta.get((b, a))


def run_ppr(G, seed: str, edge_meta: dict | None = None, damping: float = 0.85) -> list[dict]:
    """
    以 seed 為起點跑 Personalized PageRank。
    回傳依分數排序的候選清單：
      [{"node", "score", "hops", "path", "path_reasons",
        "reasons", "mention_count", "source_diversity"}, ...]
    （不含 seed 自己；從 seed 走不到的節點會被排除，PPR 分數本來就會趨近 0）

    - path：從 seed 到該候選的實際節點鏈（例如 ["聯發科","日月光","台積電"]），
      不論幾跳都會算，用來做 explainability——多跳候選沒有單一 edge 可引用，
      但可以把整條路徑的每一段 reason 串起來給使用者看，不是黑盒。
    - path_reasons：對應 path 每一段 (from, to) 的**完整** reason 清單
      （{"from","to","reasons":[...]}），不是只取第一筆——後續 Selection
      階段要把這些原始描述整合成一句話，需要完整材料，不能只給一筆。
    - reasons/mention_count/source_diversity：只有 hops==1 時才填，是路徑長度
      為 1 時的簡化版欄位，保留給既有呼叫端相容用。
    """
    names = G.vs["name"]
    if seed not in names:
        return []

    seed_idx = names.index(seed)
    scores = G.personalized_pagerank(
        reset_vertices=[seed_idx],
        directed=G.is_directed(),
        weights="weight",
        damping=damping,
    )
    hops = G.distances(source=[seed_idx])[0]

    candidates = []
    for i, name in enumerate(names):
        if i == seed_idx:
            continue
        h = hops[i]
        if h == float("inf"):
            continue

        candidate = {
            "node": name, "score": scores[i], "hops": int(h),
            "path": [], "path_reasons": [],
            "reasons": [], "mention_count": 0, "source_diversity": 0,
        }

        try:
            vpaths = G.get_shortest_paths(seed_idx, to=i, output="vpath")
            path_idx = vpaths[0] if vpaths else []
        except Exception:
            path_idx = []
        path_names = [names[p] for p in path_idx]
        candidate["path"] = path_names

        for a, b in zip(path_names, path_names[1:]):
            meta = _edge_reason(edge_meta, a, b)
            candidate["path_reasons"].append({
                "from": a, "to": b,
                "reasons": meta.get("reasons", []) if meta else [],
            })

        if h == 1:
            meta = _edge_reason(edge_meta, seed, name)
            if meta:
                candidate["reasons"] = meta.get("reasons", [])
                candidate["mention_count"] = meta.get("weight", 0)
                candidate["source_diversity"] = len(meta.get("podcast_sources", set()))

        candidates.append(candidate)

    candidates.sort(key=lambda c: -c["score"])
    return candidates
