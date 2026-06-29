# apps/knowledge_graph/services/leiden_cluster.py
"""
Leiden 社群偵測服務

流程：
  1. 從 KG_DB 撈取 links（依日期 / relation_type / industry 篩選）
  2. 建立加權無向圖（edge weight = 出現次數）
  3. 用 Leiden 演算法做社群偵測
  4. 回傳每個社群的公司清單與其連線
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from django.db import connections


CLUSTER_COLORS = [
    "#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6",
    "#06b6d4", "#f97316", "#84cc16", "#e11d48", "#14b8a6",
    "#a78bfa", "#34d399", "#fbbf24", "#fb7185", "#38bdf8",
    "#818cf8", "#2dd4bf", "#fb923c", "#f472b6", "#a3e635",
]


# ── 1. 資料查詢 ────────────────────────────────────────────────────────────

def fetch_links(
    start_date: Optional[str],
    end_date: Optional[str],
    relation_type: Optional[str],
    industry: Optional[str],
) -> list[dict]:
    with connections["knowledge_graphdb"].cursor() as cursor:
        cursor.execute("SELECT name, industry FROM nodes")
        node_info = {row[0]: (row[1] or "") for row in cursor.fetchall()}

        sql = "SELECT source, target, relation_type, reason FROM links WHERE 1=1"
        params: list = []

        if start_date and end_date:
            sql += " AND summary_date BETWEEN %s AND %s"
            params.extend([start_date, end_date])
        elif start_date:
            sql += " AND summary_date >= %s"
            params.append(start_date)
        elif end_date:
            sql += " AND summary_date <= %s"
            params.append(end_date)

        if relation_type:
            sql += " AND relation_type = %s"
            params.append(relation_type)

        cursor.execute(sql, params)
        raw = cursor.fetchall()

    if industry:
        kw = industry.lower()
        raw = [
            (s, t, rt, r) for s, t, rt, r in raw
            if kw in node_info.get(s, "").lower() or kw in s.lower()
            or kw in node_info.get(t, "").lower() or kw in t.lower()
        ]

    return [
        {
            "source": s, "target": t,
            "relation_type": rt or "", "reason": r or "",
            "source_industry": node_info.get(s, ""),
            "target_industry": node_info.get(t, ""),
        }
        for s, t, rt, r in raw
    ]


# ── 2. Leiden 分群 ─────────────────────────────────────────────────────────

def run_leiden(
    links: list[dict],
    resolution: float = 1.0,
    min_community_size: int = 3,
) -> dict:
    """
    對 links 建圖後執行 Leiden 社群偵測。

    回傳格式：
    {
        communities : list[dict]
            cluster_id, color, companies, relations, size
        nodes       : list[dict]   — D3 nodes
        edges       : list[dict]   — D3 links（含 weight）
        meta        : dict
    }
    """
    import igraph as ig
    import leidenalg

    if not links:
        return {"communities": [], "nodes": [], "edges": [], "meta": {"total_links": 0}}

    # ── 建加權邊表 ─────────────────────────────────────────────────────────
    edge_weight: dict[tuple[str, str], int] = defaultdict(int)
    edge_reasons: dict[tuple[str, str], list[str]] = defaultdict(list)
    edge_rtypes:  dict[tuple[str, str], list[str]] = defaultdict(list)

    for lk in links:
        key = tuple(sorted([lk["source"], lk["target"]]))
        edge_weight[key] += 1
        if lk["reason"]:
            edge_reasons[key].append(lk["reason"])
        edge_rtypes[key].append(lk["relation_type"])

    # ── 建 igraph ──────────────────────────────────────────────────────────
    all_companies = sorted({s for s, t in edge_weight} | {t for s, t in edge_weight})
    idx = {co: i for i, co in enumerate(all_companies)}

    edges_for_ig = list(edge_weight.keys())
    weights      = [edge_weight[e] for e in edges_for_ig]
    ig_edges     = [(idx[s], idx[t]) for s, t in edges_for_ig]

    G = ig.Graph(n=len(all_companies), edges=ig_edges, directed=False)
    G.vs["name"]   = all_companies
    G.es["weight"] = weights

    # ── Leiden ────────────────────────────────────────────────────────────
    partition = leidenalg.find_partition(
        G,
        leidenalg.RBConfigurationVertexPartition,
        weights="weight",
        resolution_parameter=resolution,
        seed=42,
    )

    # community index for each vertex
    membership = partition.membership   # list[int], len = len(all_companies)

    # ── 整理社群 ──────────────────────────────────────────────────────────
    community_map: dict[int, list[str]] = defaultdict(list)
    for co, cid in zip(all_companies, membership):
        community_map[cid].append(co)

    # 依大小排序；過濾太小的社群
    sorted_communities = sorted(community_map.items(), key=lambda kv: -len(kv[1]))
    valid_communities  = [(cid, cos) for cid, cos in sorted_communities if len(cos) >= min_community_size]

    # 重新編號並附色
    communities_out: list[dict] = []
    company_to_new_cid: dict[str, int] = {}

    for new_cid, (orig_cid, companies) in enumerate(valid_communities):
        for co in companies:
            company_to_new_cid[co] = new_cid
        communities_out.append({
            "cluster_id": new_cid,
            "color":      CLUSTER_COLORS[new_cid % len(CLUSTER_COLORS)],
            "companies":  sorted(companies),
            "size":       len(companies),
            "relations":  [],   # 填在下方
        })

    # ── 整理 D3 邊（只保留兩端都在 valid community 裡的） ──────────────
    d3_edges: list[dict] = []
    for (s, t), w in edge_weight.items():
        if s not in company_to_new_cid or t not in company_to_new_cid:
            continue
        sc, tc = company_to_new_cid[s], company_to_new_cid[t]
        key = tuple(sorted([s, t]))
        d3_edges.append({
            "source":        s,
            "target":        t,
            "weight":        w,
            "same_cluster":  sc == tc,
            "relation_type": edge_rtypes[key][0] if edge_rtypes[key] else "",
            "reasons":       edge_reasons[key][:2],
        })

    # 把 edges 分配回 community.relations（社群內的邊）
    for edge in d3_edges:
        if edge["same_cluster"]:
            cid = company_to_new_cid[edge["source"]]
            if cid < len(communities_out):
                communities_out[cid]["relations"].append(edge)

    # ── D3 nodes ──────────────────────────────────────────────────────────
    # 計算每個節點的 degree（在 valid 邊中）
    degree: dict[str, int] = defaultdict(int)
    for e in d3_edges:
        degree[e["source"]] += e["weight"]
        degree[e["target"]] += e["weight"]

    d3_nodes: list[dict] = []
    for co in all_companies:
        if co not in company_to_new_cid:
            continue
        cid = company_to_new_cid[co]
        d3_nodes.append({
            "id":         co,
            "cluster_id": cid,
            "color":      CLUSTER_COLORS[cid % len(CLUSTER_COLORS)],
            "degree":     degree[co],
        })

    noise_companies = [co for co in all_companies if co not in company_to_new_cid]

    meta = {
        "total_links":     len(links),
        "total_companies": len(all_companies),
        "num_communities": len(communities_out),
        "noise_companies": len(noise_companies),
        "resolution":      resolution,
        "modularity":      round(partition.modularity, 4),
    }

    return {
        "communities": communities_out,
        "nodes":       d3_nodes,
        "edges":       d3_edges,
        "meta":        meta,
    }


# ── 3. 兩期比較 ────────────────────────────────────────────────────────────

_UNMATCHED_A = "#52525b"
_NEW_B        = "#4ade80"


def run_leiden_diff(
    links_a: list[dict],
    links_b: list[dict],
    resolution: float = 1.0,
    min_community_size: int = 3,
) -> dict:
    """
    分別對兩期 links 執行 Leiden，再用 Jaccard 配對社群。
    配對到的社群兩邊同色；A 期無對應 → 灰色；B 期新社群 → 亮綠色。
    """
    def _safe(links):
        if not links:
            return {"communities": [], "nodes": [], "edges": [], "meta": {}}
        try:
            return run_leiden(links, resolution, min_community_size)
        except Exception:
            return {"communities": [], "nodes": [], "edges": [], "meta": {}}

    result_a = _safe(links_a)
    result_b = _safe(links_b)

    comm_a = result_a["communities"]
    comm_b = result_b["communities"]

    # ── Greedy Jaccard 配對 ───────────────────────────────────────────
    def jaccard(s1: set, s2: set) -> float:
        u = s1 | s2
        return len(s1 & s2) / len(u) if u else 0.0

    pairs: list[tuple[float, int, int]] = []
    for bi, cb in enumerate(comm_b):
        sb = set(cb["companies"])
        for ai, ca in enumerate(comm_a):
            sc = jaccard(sb, set(ca["companies"]))
            if sc > 0:
                pairs.append((sc, bi, ai))
    pairs.sort(reverse=True)

    matched_b: dict[int, int] = {}   # b_idx → a cluster_id
    used_a: set[int] = set()
    used_b: set[int] = set()
    for sc, bi, ai in pairs:
        if bi in used_b or ai in used_a:
            continue
        if sc >= 0.10:
            matched_b[bi] = comm_a[ai]["cluster_id"]
            used_a.add(ai)
            used_b.add(bi)

    # ── 重新著色：配對 pair 共用同色 ─────────────────────────────────
    a_cid_to_color: dict[int, str] = {}
    b_idx_to_color: dict[int, str] = {}
    for bi, a_cid in matched_b.items():
        col = CLUSTER_COLORS[bi % len(CLUSTER_COLORS)]
        b_idx_to_color[bi]   = col
        a_cid_to_color[a_cid] = col

    for ca in comm_a:
        ca["color"] = a_cid_to_color.get(ca["cluster_id"], _UNMATCHED_A)
    for bi, cb in enumerate(comm_b):
        cb["color"]   = b_idx_to_color.get(bi, _NEW_B)
        cb["is_new"]  = bi not in matched_b

    # 同步 node 顏色
    a_cid_col = {ca["cluster_id"]: ca["color"] for ca in comm_a}
    b_cid_col = {cb["cluster_id"]: cb["color"] for cb in comm_b}
    for n in result_a["nodes"]:
        n["color"] = a_cid_col.get(n["cluster_id"], _UNMATCHED_A)
    for n in result_b["nodes"]:
        n["color"] = b_cid_col.get(n["cluster_id"], _NEW_B)

    b_matched_a_ids = set(matched_b.values())
    disappeared = sum(1 for ca in comm_a if ca["cluster_id"] not in b_matched_a_ids)

    return {
        "communities_a": comm_a,
        "nodes_a":       result_a["nodes"],
        "edges_a":       result_a["edges"],
        "communities_b": comm_b,
        "nodes_b":       result_b["nodes"],
        "edges_b":       result_b["edges"],
        "matched_b":     {str(bi): a_cid for bi, a_cid in matched_b.items()},
        "meta": {
            "new_communities":          sum(1 for bi in range(len(comm_b)) if bi not in matched_b),
            "continued_communities":    len(matched_b),
            "disappeared_communities":  disappeared,
            "modularity_a": result_a["meta"].get("modularity", 0),
            "modularity_b": result_b["meta"].get("modularity", 0),
            "num_communities_a": len(comm_a),
            "num_communities_b": len(comm_b),
        },
    }
