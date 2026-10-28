# apps/knowledge_graph/services/ppr_utils.py
"""
Personalized PageRank 共用工具，供 Supply / Co-impact 策略共用。
"""
from __future__ import annotations


def _edge_reason(edge_meta: dict | None, a: str, b: str) -> dict | None:
    if edge_meta is None:
        return None
    return edge_meta.get((a, b)) or edge_meta.get((b, a))


def _path_support(path_names: list[str], edge_meta: dict | None) -> tuple[int, int]:
    """整條路徑累計的 (不同集數, 提及次數)；集數跨段去重，同一集談到兩段只算一集。"""
    sources: set[str] = set()
    mentions = 0
    for a, b in zip(path_names, path_names[1:]):
        meta = _edge_reason(edge_meta, a, b)
        if meta:
            sources |= meta.get("podcast_sources", set())
            mentions += meta.get("weight", 0)
    return len(sources), mentions


def _best_shortest_path(G, seed_idx: int, target_idx: int, edge_meta: dict | None) -> list[str]:
    """
    從 seed 到目標的「步數最少」路徑可能不只一條，挑其中累計集數最多的；
    集數一樣就比累計提及次數，再一樣就依節點名稱排序（讓結果每次都一樣）。
    """
    names = G.vs["name"]
    paths = [[names[p] for p in path] for path in G.get_all_shortest_paths(seed_idx, to=target_idx)]
    if not paths:
        return []

    def rank(path: list[str]):
        episodes, mentions = _path_support(path, edge_meta)
        return (-episodes, -mentions, path)

    return min(paths, key=rank)


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
      只在步數最少的路徑裡挑：有多條時選累計集數最多的，其次累計提及次數，再其次依名稱排序。
      這只影響畫面上顯示哪一條，不影響 PPR 分數與排名。
    - path_reasons：對應 path 每一段 (from, to) 的**完整** reason 清單
      （{"from","to","reasons":[...]}），不是只取第一筆——後續 Selection
      階段要把這些原始描述整合成一句話，需要完整材料，不能只給一筆。
    - reasons：只有 hops==1 時才填，是路徑長度為 1 時的簡化版欄位，保留給既有呼叫端相容用。
    - mention_count/source_diversity：hops==1 是跟 seed 直接那一條線的次數與集數；
      hops>1（間接）是整條路徑每一段的累計（次數相加、集數去重）。每一段自己的
      數字與每一筆原始提及放在 path_reasons 各段的 mention_count/source_diversity/rows。
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
            path_names = _best_shortest_path(G, seed_idx, i, edge_meta)
        except Exception:
            path_names = []
        candidate["path"] = path_names

        path_sources: set[str] = set()
        path_mentions = 0
        for a, b in zip(path_names, path_names[1:]):
            meta = _edge_reason(edge_meta, a, b)
            segment_sources = meta.get("podcast_sources", set()) if meta else set()
            segment_mentions = meta.get("weight", 0) if meta else 0
            candidate["path_reasons"].append({
                "from": a, "to": b,
                "reasons": meta.get("reasons", []) if meta else [],
                # 這一段自己的提及次數 / 集數 / 每一筆原始提及，給前端逐段顯示
                "mention_count": segment_mentions,
                "source_diversity": len(segment_sources),
                "rows": meta.get("rows", []) if meta else [],
            })
            path_sources |= segment_sources
            path_mentions += segment_mentions

        if h == 1:
            meta = _edge_reason(edge_meta, seed, name)
            if meta:
                candidate["reasons"] = meta.get("reasons", [])
                candidate["mention_count"] = meta.get("weight", 0)
                candidate["source_diversity"] = len(meta.get("podcast_sources", set()))
        else:
            # 間接標的跟 seed 之間沒有直接那一條線，改成整條路徑每一段累計：
            # 次數 = 各段次數相加；集數 = 各段出自的不同集數去重後的總數（同一集同時談到兩段只算一集）。
            # reasons 仍維持空，Selection 階段靠它判斷「多跳候選沒有單一 reason 可查」直接放行。
            candidate["mention_count"] = path_mentions
            candidate["source_diversity"] = len(path_sources)

        candidates.append(candidate)

    candidates.sort(key=lambda c: -c["score"])
    return candidates
