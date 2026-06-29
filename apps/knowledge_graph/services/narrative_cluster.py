# apps/knowledge_graph/services/narrative_cluster.py
"""
Reason Embedding + HDBSCAN 敘事群聚服務

流程：
  1. 從 KG_DB 依日期 / relation_type / industry 條件撈取 links
  2. 對每條 link 的 reason 做 bge-m3 embedding
  3. 用 HDBSCAN 分群
  4. 用 plurality vote 讓每家公司只屬於一個 cluster（邊數最多的那個）
  5. 選用 LLM 自動命名每個 cluster 主題
"""
from __future__ import annotations

import json
from typing import Optional

import numpy as np
from django.db import connections


# ── 1. 資料查詢 ────────────────────────────────────────────────────────────────

def fetch_links_for_cluster(
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
            "source":        s,
            "target":        t,
            "relation_type": rt or "",
            "reason":        r  or "",
        }
        for s, t, rt, r in raw
    ]


# ── 2. Embedding + HDBSCAN + 獨佔公司分配 ────────────────────────────────────

def run_reason_clustering(
    links: list[dict],
    min_cluster_size: int = 2,
) -> list[dict]:
    """
    對 links 的 reason 做 bge-m3 embedding，再用 HDBSCAN 分群。
    最後用 plurality vote 讓每家公司只屬於邊數最多的那個 cluster。

    每個 cluster dict 格式：
    {
        cluster_id       : int   (-1 = noise)
        theme_label      : str
        companies        : list[str]   (可重複，所有出現過的公司)
        exclusive_companies: list[str] (每家公司只出現一次，plurality vote 決定)
        relations        : list[dict]
        sample_reasons   : list[str]
        is_noise         : bool
        size             : int
    }
    """
    with_reason    = [l for l in links if l["reason"].strip()]
    without_reason = [l for l in links if not l["reason"].strip()]

    if len(with_reason) < 2:
        clusters = [_make_cluster(
            cluster_id=0,
            relations=with_reason + without_reason,
            is_noise=False,
        )]
        return _assign_exclusive_companies(clusters)

    from apps.ai_assistant.management.commands.build_podcast_embeddings import embed_texts
    import hdbscan as hdbscan_lib
    import umap as umap_lib

    reasons  = [l["reason"] for l in with_reason]
    vectors  = np.array(embed_texts(reasons), dtype=np.float32)

    # ── UMAP 降維：1024 → 10 維（資料少時自動縮減目標維度）─────────────────────
    n_neighbors  = min(15, len(reasons) - 1)
    n_components = min(10, len(reasons) - 2)  # 必須 < N，否則譜分解報錯

    if n_components >= 2:
        reducer = umap_lib.UMAP(
            n_components=n_components,
            n_neighbors=min(n_neighbors, max(2, len(reasons) // 5)),  # 偏局部結構
            min_dist=0.1,    # 0.0 會過度壓縮，給群之間留一點空間
            metric="cosine",
            random_state=42,
            init="random",
        )
        reduced = reducer.fit_transform(vectors)
    else:
        reduced = vectors    # 資料極少（< 4 條），直接用原始向量

    # ── HDBSCAN 在低維空間分群 ────────────────────────────────────────────────
    # 門檻用較保守的 √n / 2，避免合併過度
    effective_min = max(3, int(len(reasons) ** 0.5) // 2)

    clusterer = hdbscan_lib.HDBSCAN(
        min_cluster_size=effective_min,
        min_samples=1,
        metric="euclidean",
        cluster_selection_method="leaf",  # leaf 比 eom 更傾向切出多個小群
    )
    labels: np.ndarray = clusterer.fit_predict(reduced)

    cluster_map: dict[int, list[dict]] = {}
    for link, label in zip(with_reason, labels.tolist()):
        cluster_map.setdefault(int(label), []).append(link)

    if without_reason:
        cluster_map.setdefault(-1, []).extend(without_reason)

    noise_links = cluster_map.pop(-1, [])
    sorted_items = sorted(cluster_map.items(), key=lambda kv: -len(kv[1]))

    result: list[dict] = []
    for cid, rels in sorted_items:
        result.append(_make_cluster(cluster_id=cid, relations=rels, is_noise=False))
    if noise_links:
        result.append(_make_cluster(cluster_id=-1, relations=noise_links, is_noise=True))

    # 每家公司只屬於一個 cluster
    return _assign_exclusive_companies(result)


# ── 3. Plurality vote + 小群合併 ──────────────────────────────────────────────

def _assign_exclusive_companies(clusters: list[dict]) -> list[dict]:
    """
    Plurality vote：每家公司分配到它出現邊數最多的 cluster（非 noise 優先）。
    結果寫入各 cluster 的 exclusive_companies 欄位。
    群的大小不在這裡過濾，由前端決定是否顯示。
    """
    # ── 統計各公司在各 cluster 的邊數 ────────────────────────────────────────
    votes: dict[str, dict[int, int]] = {}
    for cluster in clusters:
        cid = cluster["cluster_id"]
        for rel in cluster["relations"]:
            for company in (rel["source"], rel["target"]):
                votes.setdefault(company, {})
                votes[company][cid] = votes[company].get(cid, 0) + 1

    # ── 每家公司去邊數最多的 cluster（非 noise 優先）──────────────────────────
    assignment: dict[str, int] = {}
    for company, vote_map in votes.items():
        best = max(vote_map.keys(), key=lambda cid: (cid != -1, vote_map[cid]))
        assignment[company] = best

    # ── 寫回各 cluster ────────────────────────────────────────────────────────
    exclusive_map: dict[int, list[str]] = {}
    for company, cid in assignment.items():
        exclusive_map.setdefault(cid, []).append(company)

    for cluster in clusters:
        cid = cluster["cluster_id"]
        cluster["exclusive_companies"] = sorted(exclusive_map.get(cid, []))

    return clusters


# ── 4. 兩期比較 ───────────────────────────────────────────────────────────────

def run_cluster_diff(
    links_a: list[dict],
    links_b: list[dict],
) -> dict:
    """
    比較兩期 clustering 結果。

    回傳格式：
    {
        clusters_b        : list[dict]  — B 期 cluster，每家公司加上 status
        disappeared_themes: list[dict]  — A 期有但 B 期消失的 cluster
        meta              : dict        — 統計數字
    }

    status 值：
        "new"      — 該公司在 A 期完全沒出現
        "stable"   — 在 A 期同一個 matched cluster 裡
        "shifted"  — 在 A 期有出現但在不同 cluster
    """
    # 先確保有足夠資料，否則回傳空結果
    def _safe_cluster(links):
        if not links:
            return []
        try:
            return run_reason_clustering(links)
        except Exception:
            return []

    clusters_a = _safe_cluster(links_a)
    clusters_b = _safe_cluster(links_b)

    MIN_CO = 5  # 前端顯示門檻（與 narrative_cluster.html 一致）

    visible_a = [c for c in clusters_a if not c["is_noise"] and len(c["exclusive_companies"]) >= MIN_CO]
    visible_b = [c for c in clusters_b if not c["is_noise"] and len(c["exclusive_companies"]) >= MIN_CO]

    # ── 建立 A 期公司 → cluster_id 對應 ─────────────────────────────────────
    company_to_cluster_a: dict[str, int] = {}
    for ca in visible_a:
        for co in ca["exclusive_companies"]:
            company_to_cluster_a[co] = ca["cluster_id"]

    companies_a_set = set(company_to_cluster_a.keys())

    # ── Greedy Jaccard 配對：B cluster → 最佳 A cluster ──────────────────────
    def jaccard(set1: set, set2: set) -> float:
        union = set1 | set2
        return len(set1 & set2) / len(union) if union else 0.0

    # 計算所有配對的 Jaccard 分數
    pairs: list[tuple[float, int, int]] = []  # (score, b_idx, a_idx)
    for bi, cb in enumerate(visible_b):
        sb = set(cb["exclusive_companies"])
        for ai, ca in enumerate(visible_a):
            sa = set(ca["exclusive_companies"])
            sc = jaccard(sb, sa)
            if sc > 0:
                pairs.append((sc, bi, ai))

    pairs.sort(reverse=True)

    matched_b: dict[int, int] = {}  # b_idx → a cluster_id
    used_a: set[int] = set()
    used_b: set[int] = set()

    for sc, bi, ai in pairs:
        if bi in used_b or ai in used_a:
            continue
        if sc >= 0.10:   # 至少 10% 重疊才算同一主題
            matched_b[bi] = visible_a[ai]["cluster_id"]
            used_a.add(ai)
            used_b.add(bi)

    # ── 為 B 期 cluster 加上 is_new_theme 與每家公司的 status ────────────────
    b_matched_a_ids = set(matched_b.values())

    enriched_b = []
    for bi, cb in enumerate(visible_b):
        is_new_theme = bi not in matched_b
        matched_a_cid = matched_b.get(bi)  # A 期對應 cluster_id

        company_status: dict[str, str] = {}
        for co in cb["exclusive_companies"]:
            if co not in companies_a_set:
                company_status[co] = "new"
            elif company_to_cluster_a.get(co) == matched_a_cid:
                company_status[co] = "stable"
            else:
                company_status[co] = "shifted"

        enriched = dict(cb)
        enriched["is_new_theme"] = is_new_theme
        enriched["company_status"] = company_status
        enriched_b.append(enriched)

    # ── A 期有但 B 期消失的主題 ─────────────────────────────────────────────
    disappeared = [
        {"companies": ca["exclusive_companies"], "size": ca["size"]}
        for ai, ca in enumerate(visible_a)
        if ca["cluster_id"] not in b_matched_a_ids
    ]

    # ── 統計 ─────────────────────────────────────────────────────────────────
    all_b_companies = {co for cb in visible_b for co in cb["exclusive_companies"]}
    new_cos  = sum(1 for cb in enriched_b for co, st in cb["company_status"].items() if st == "new")
    gone_cos = len(companies_a_set - all_b_companies)

    meta = {
        "new_themes":          sum(1 for cb in enriched_b if cb["is_new_theme"]),
        "continued_themes":    sum(1 for cb in enriched_b if not cb["is_new_theme"]),
        "disappeared_themes":  len(disappeared),
        "new_companies":       new_cos,
        "disappeared_companies": gone_cos,
        "total_b_clusters":    len(visible_b),
    }

    # 為 A 期 cluster 加上配對資訊（b 期有沒有對應 cluster）
    a_id_to_color_slot: dict[int, int] = {}
    for bi, cb in enumerate(visible_b):
        if bi in matched_b:
            a_cid = matched_b[bi]
            a_id_to_color_slot[a_cid] = bi   # 用 B 的 index 當 color slot

    enriched_a = []
    for ai, ca in enumerate(visible_a):
        enriched = dict(ca)
        enriched["matched_b_idx"] = next(
            (bi for bi, a_cid in matched_b.items() if a_cid == ca["cluster_id"]), None
        )
        enriched_a.append(enriched)

    return {
        "clusters_a":          enriched_a,
        "clusters_b":          enriched_b,
        "disappeared_themes":  disappeared,
        "meta":                meta,
        "matched_b":           matched_b,   # {b_idx: a_cluster_id}
    }


# ── 5. LLM 自動命名 ────────────────────────────────────────────────────────────

def name_clusters_with_llm(
    clusters: list[dict],
    client,
    model: str,
) -> list[dict]:
    for c in clusters:
        if c["is_noise"] or not c["sample_reasons"]:
            c["theme_label"] = "未分群"
            continue

        reasons_text = json.dumps(c["sample_reasons"], ensure_ascii=False)
        prompt = (
            "以下是幾條描述公司間商業關係的敘述，它們屬於同一個市場敘事群：\n"
            f"{reasons_text}\n\n"
            "請用 4–8 個繁體中文字，為這群敘述命名一個投資主題（例如：AI基礎建設、EV供應鏈）。"
            "只輸出主題名稱，不要其他文字。"
        )
        try:
            resp = client.models.generate_content(model=model, contents=prompt)
            c["theme_label"] = (getattr(resp, "text", "") or "").strip()[:24]
        except Exception:
            c["theme_label"] = (c["sample_reasons"][0])[:20] + "…"

    return clusters


# ── 內部工具 ───────────────────────────────────────────────────────────────────

def _collect_companies(relations: list[dict]) -> list[str]:
    seen: set[str] = set()
    for r in relations:
        seen.add(r["source"])
        seen.add(r["target"])
    return sorted(seen)


def _make_cluster(cluster_id: int, relations: list[dict], is_noise: bool) -> dict:
    companies = _collect_companies(relations)
    sample    = [r["reason"] for r in relations if r["reason"].strip()][:3]
    return {
        "cluster_id":          cluster_id,
        "theme_label":         "",
        "companies":           companies,
        "exclusive_companies": [],   # 由 _assign_exclusive_companies 填入
        "relations":           relations,
        "sample_reasons":      sample,
        "is_noise":            is_noise,
        "size":                len(relations),
    }
