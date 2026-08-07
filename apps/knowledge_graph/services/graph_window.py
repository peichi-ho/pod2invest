# apps/knowledge_graph/services/graph_window.py
"""
Rolling Window 圖譜快照服務

流程：
  1. 給定截止日期（end_date）與窗口長度（window_days），
     從 knowledge_graphdb 撈出 [end_date - window_days, end_date] 區間內的 links
  2. 將同一組 (source, target, relation_type) 的多筆 link 聚合成一條加權邊，
     權重（weight）= 該窗口內被提及的次數（mention_count）
  3. 依聚合結果建立 igraph 圖，供 Supply / Substitute / Co-impact 策略共用

供三種策略共用，各自再依 relation_type 篩選、決定 directed/undirected。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from django.db import connections


def fetch_links_in_window(
    end_date: date,
    window_days: int,
    relation_type: Optional[str] = None,
) -> list[dict]:
    """
    撈取 [end_date - window_days, end_date] 區間內的 links（含 summary_date、podcast_source，
    供後續計算 mention_count / source 多樣性使用）。
    """
    start_date = end_date - timedelta(days=window_days)

    sql = (
        "SELECT source, target, relation_type, reason, summary_date, podcast_source "
        "FROM links WHERE summary_date BETWEEN %s AND %s"
    )
    params: list = [start_date, end_date]

    if relation_type:
        sql += " AND relation_type = %s"
        params.append(relation_type)

    with connections["knowledge_graphdb"].cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    return [
        {
            "source": s,
            "target": t,
            "relation_type": rt or "",
            "reason": r or "",
            "summary_date": sd,
            "podcast_source": ps or "",
        }
        for s, t, rt, r, sd, ps in rows
    ]


def build_weighted_graph(links: list[dict], directed: bool = True):
    """
    將 links 依 (source, target, relation_type) 聚合成加權邊：
      weight = mention_count（該窗口內被提及次數）
    directed=False 時，(A,B) 與 (B,A) 會被視為同一條邊（用於 Substitute / Co-impact）。
    directed=True 時保留原始方向（用於 Supply）。

    回傳 (igraph.Graph, edge_meta)：
      edge_meta[(source, target)] = {
          "weight": int,
          "reasons": list[str],
          "podcast_sources": set[str],   # 提及來源的集數數量 = source 多樣性
      }
    """
    import igraph as ig

    edge_agg: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"weight": 0, "reasons": [], "podcast_sources": set()}
    )

    for lk in links:
        key = (lk["source"], lk["target"]) if directed else tuple(sorted([lk["source"], lk["target"]]))
        entry = edge_agg[key]
        entry["weight"] += 1  # mention_count 仍計入每一筆原始提及，不受下面的 reasons 去重影響
        if lk["reason"] and lk["reason"] not in entry["reasons"]:
            entry["reasons"].append(lk["reason"])
        if lk["podcast_source"]:
            entry["podcast_sources"].add(lk["podcast_source"])

    all_nodes = sorted({n for pair in edge_agg for n in pair})
    idx = {n: i for i, n in enumerate(all_nodes)}

    ig_edges = [(idx[s], idx[t]) for s, t in edge_agg]
    weights = [edge_agg[e]["weight"] for e in edge_agg]

    G = ig.Graph(n=len(all_nodes), edges=ig_edges, directed=directed)
    G.vs["name"] = all_nodes
    G.es["weight"] = weights

    return G, dict(edge_agg)
