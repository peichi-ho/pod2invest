"""
apps/knowledge_graph/services/asset_kg_score.py

探索相關標的（Basket）卡片上的「Podcast 看法」分數：某個標的在
[end_date - window_days, end_date] 內，節目提到的產業關係整體對它偏多還是偏空。

做法：
    - 抓窗口內這個標的在 source 或 target 的所有連線（不只跟查詢種子之間的連線）
    - 每條連線用 LLM 判斷對「這個標的本身」是利多 / 利空 / 中性、影響強 / 弱，
      直接沿用 thesis_score 籃子模式的 _judge_company_impact（含它的記憶體快取）
    - 每條連線的份量 = 關係量級 / 15 × 強度（量級同 thesis_score.MAGNITUDES）
    - score = 50 + 50 × (P − N) / (P + N + K)，P / N 是利多 / 利空份量加總，
      K 跟 thesis_score 籃子模式的單一公司狀況一樣用 BASKET_COMPANY_SHRINK，
      證據少時往 50 收縮，連線再多也不會超出 0~100

這是描述性指標：2026-10 以 19 個截止日、約一萬筆判斷回測，分數跟窗口期間的
超額報酬一致（偏空組 -1.4%、偏多組 +4.3%），但跟之後 5 / 20 個交易日的漲跌
沒有關係，前端不能當成漲跌預測呈現。

判斷結果目前只存在記憶體（程式重啟就重算），還沒有建資料表。
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Optional

from django.db import connections

from apps.knowledge_graph.services.thesis_score import (
    BASKET_COMPANY_SHRINK,
    BASKET_MAGNITUDE_UNIT,
    MAGNITUDES,
    STRENGTH_WEIGHT,
    _describe_cluster,
    _judge_company_impact,
)

BULLISH_THRESHOLD = 60
BEARISH_THRESHOLD = 40
# 一次送 LLM 判斷的連線數；太多時小模型輸出容易被截斷
JUDGE_BATCH_SIZE = 40
JUDGE_WORKERS = 4
TOP_REASONS = 3


def fetch_asset_links(nodes: list[str], end_date: date, window_days: int) -> list[dict]:
    """窗口內 source 或 target 落在 nodes 的連線。日期區間跟 graph_window.fetch_links_in_window 一致（含頭尾）。"""
    if not nodes:
        return []
    start_date = end_date - timedelta(days=window_days)
    relation_types = list(MAGNITUDES)
    with connections["knowledge_graphdb"].cursor() as cursor:
        cursor.execute(
            """
            SELECT id, source, target, relation_type, reason, summary_date, podcast_source
            FROM links
            WHERE (source = ANY(%s) OR target = ANY(%s))
              AND summary_date BETWEEN %s AND %s
              AND relation_type = ANY(%s)
            """,
            [nodes, nodes, start_date, end_date, relation_types],
        )
        rows = cursor.fetchall()
    return [
        {
            "id": lid, "source": s, "target": t, "relation_type": rt,
            "reason": r or "", "summary_date": sd, "podcast_source": ps or "",
        }
        for lid, s, t, rt, r, sd, ps in rows
    ]


def _judge_items(items: list[tuple[tuple, str]]) -> list[Optional[dict]]:
    """分批、平行呼叫 _judge_company_impact，回傳與 items 等長的判斷結果。"""
    batches = [items[i:i + JUDGE_BATCH_SIZE] for i in range(0, len(items), JUDGE_BATCH_SIZE)]
    if not batches:
        return []
    with ThreadPoolExecutor(max_workers=min(JUDGE_WORKERS, len(batches))) as ex:
        results = list(ex.map(_judge_company_impact, batches))
    return [j for batch in results for j in batch]


def score_from_evidence(evidence: list[dict]) -> dict:
    """
    evidence：[{"relation_type", "impact", "strength"}]，impact 為 None 代表判斷失敗、不計分。
    回傳 {"score", "label", "positive", "negative"}；沒有任何連線時 score 為 None。
    """
    if not evidence:
        return {"score": None, "label": "無資料", "positive": 0.0, "negative": 0.0}
    pos = neg = 0.0
    for e in evidence:
        if e.get("impact") not in ("positive", "negative"):
            continue
        amount = (
            MAGNITUDES.get(e["relation_type"], 0) / BASKET_MAGNITUDE_UNIT
            * STRENGTH_WEIGHT.get(e.get("strength"), STRENGTH_WEIGHT["weak"])
        )
        if e["impact"] == "positive":
            pos += amount
        else:
            neg += amount
    score = round(50 + 50 * (pos - neg) / (pos + neg + BASKET_COMPANY_SHRINK))
    if score >= BULLISH_THRESHOLD:
        label = "偏多"
    elif score <= BEARISH_THRESHOLD:
        label = "偏空"
    else:
        label = "中性"
    return {"score": score, "label": label, "positive": round(pos, 2), "negative": round(neg, 2)}


def _top_reasons(evidence: list[dict], impact: str) -> list[dict]:
    picked = sorted(
        (e for e in evidence if e.get("impact") == impact),
        key=lambda e: (e.get("strength") != "strong", -e["summary_date"].toordinal()),
    )
    out, seen = [], set()
    for e in picked:
        if e["reason"] in seen:
            continue
        seen.add(e["reason"])
        out.append({
            "reason": e["reason"],
            "neighbor": e["neighbor"],
            "relation_type": e["relation_type"],
            "summary_date": e["summary_date"].isoformat(),
            "podcast_source": e["podcast_source"],
        })
        if len(out) == TOP_REASONS:
            break
    return out


def compute_asset_scores(nodes: list[str], end_date: date, window_days: int) -> dict[str, dict]:
    """
    回傳 {node: {"score", "label", "positive", "negative", "link_count", "episode_count",
                 "unjudged_count", "bullish_reasons", "bearish_reasons"}}。
    """
    node_set = set(nodes)
    links = fetch_asset_links(nodes, end_date, window_days)

    evidence_by_node: dict[str, list[dict]] = defaultdict(list)
    items = []
    for link in links:
        for asset, neighbor, asset_is_source in (
            (link["source"], link["target"], True),
            (link["target"], link["source"], False),
        ):
            if asset not in node_set:
                continue
            desc = f"[{asset}] " + _describe_cluster(
                asset, neighbor, link["relation_type"],
                [{"reason": link["reason"], "asset_is_source": asset_is_source}],
            )
            # key 帶 reason：同一條連線的理由被改寫過就重新判斷
            items.append((("asset_kg_score", link["id"], asset, link["reason"]), desc))
            evidence_by_node[asset].append({**link, "neighbor": neighbor})

    by_key = {key: judgment for (key, _), judgment in zip(items, _judge_items(items))}
    for asset, evidence in evidence_by_node.items():
        for e in evidence:
            j = by_key.get(("asset_kg_score", e["id"], asset, e["reason"]))
            e["impact"] = j.get("impact") if j else None
            e["strength"] = j.get("strength") if j else None

    results = {}
    for node in nodes:
        evidence = evidence_by_node.get(node, [])
        results[node] = {
            **score_from_evidence(evidence),
            "link_count": len(evidence),
            "episode_count": len({e["podcast_source"] for e in evidence if e["podcast_source"]}),
            "unjudged_count": sum(1 for e in evidence if e["impact"] is None),
            "bullish_reasons": _top_reasons(evidence, "positive"),
            "bearish_reasons": _top_reasons(evidence, "negative"),
        }
    return results
