import json
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from django.db import connections

from .generate import process_new_podcast, process_all_summaries
from .filter import (
    get_graph_data, generate_graph_narrative, get_latest_date_in_db,
    get_graph_diff, generate_graph_diff_narrative, _get_client,
)
from .generate_narrative import (
    process_one_narrative, process_all_narrative,
    list_summaries_with_arguments, analyze_from_summary, test_one_episode,
)


def graph_page(request):
    """渲染知識圖譜互動頁面。"""
    return render(request, "knowledge_graph/graph.html")


def node_episodes_api(request):
    """回傳某節點在最近 N 天內出現過的 podcast_source 清單。"""
    node = request.GET.get("node", "").strip()
    days = int(request.GET.get("days", 30))
    if not node:
        return JsonResponse({"error": "node is required"}, status=400)
    try:
        with connections["knowledge_graphdb"].cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT podcast_source FROM links
                WHERE (source = %s OR target = %s)
                AND summary_date >= NOW() - INTERVAL '%s days'
                AND podcast_source IS NOT NULL AND podcast_source <> ''
            """, [node, node, days])
            sources = [row[0] for row in cursor.fetchall()]
        return JsonResponse({"sources": sources, "node": node})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def latest_date_api(request):
    """回傳 links 表中最新的 summary_date，供前端設定預設日期範圍。"""
    try:
        latest = get_latest_date_in_db()
        return JsonResponse({"latest_date": latest.isoformat()})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def hot_nodes_api(request):
    """回傳最近 N 天內出現最多次的節點（source+target 合計）。"""
    days = int(request.GET.get("days", 30))
    limit = int(request.GET.get("limit", 12))
    try:
        with connections["knowledge_graphdb"].cursor() as cursor:
            cursor.execute("""
                SELECT name, SUM(cnt) as total FROM (
                    SELECT source as name, COUNT(*) as cnt FROM links
                    WHERE summary_date >= NOW() - INTERVAL '%s days' GROUP BY source
                    UNION ALL
                    SELECT target as name, COUNT(*) as cnt FROM links
                    WHERE summary_date >= NOW() - INTERVAL '%s days' GROUP BY target
                ) t GROUP BY name ORDER BY total DESC LIMIT %s
            """, [days, days, limit])
            nodes = [{"name": r[0], "count": r[1]} for r in cursor.fetchall()]
        return JsonResponse({"nodes": nodes, "days": days})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def industries_api(request):
    """回傳 nodes 表中所有不重複的產業清單（排序後）。"""
    try:
        with connections["knowledge_graphdb"].cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT industry FROM nodes "
                "WHERE industry IS NOT NULL AND industry <> '' "
                "ORDER BY industry"
            )
            industries = [row[0] for row in cursor.fetchall()]
        return JsonResponse({"industries": industries})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def graph_data_api(request):
    """
    回傳 D3.js 格式的圖譜資料（JSON）。

    Query params：
    - industry   (str, optional) : 產業關鍵字，空白表示不限
    - start_date (str, optional) : 開始日期 YYYY-MM-DD
    - end_date   (str, optional) : 結束日期 YYYY-MM-DD
    """
    industry   = request.GET.get("industry", "").strip() or None
    start_date = request.GET.get("start_date", "").strip() or None
    end_date   = request.GET.get("end_date", "").strip() or None

    try:
        data = get_graph_data(
            start_date=start_date,
            end_date=end_date,
            industry=industry,
        )
        return JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def graph_narrative_api(request):
    """
    接收前端 D3.js 圖譜資料，呼叫 Gemini 生成圖譜解讀文字。
    Body: { nodes, links, filters: { industry, start_date, end_date } }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    nodes   = body.get("nodes", [])
    links   = body.get("links", [])
    filters = body.get("filters", {})

    if not nodes and not links:
        return JsonResponse({"error": "nodes 和 links 不能都是空的"}, status=400)

    try:
        narrative = generate_graph_narrative(nodes, links, filters)
        return JsonResponse({"narrative": narrative})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def graph_diff_page(request):
    """渲染兩期差異比較頁面。"""
    return render(request, "knowledge_graph/graph_diff.html")


def graph_diff_api(request):
    """
    回傳兩期圖譜差異（D3.js 格式 + status 欄位）。

    Query params:
    - a_start, a_end   : 期間 A 日期（YYYY-MM-DD）
    - b_start, b_end   : 期間 B 日期（YYYY-MM-DD）
    - industry         : 產業關鍵字（可選）
    """
    a_start  = request.GET.get("a_start",  "").strip() or None
    a_end    = request.GET.get("a_end",    "").strip() or None
    b_start  = request.GET.get("b_start",  "").strip() or None
    b_end    = request.GET.get("b_end",    "").strip() or None
    industry = request.GET.get("industry", "").strip() or None

    if not all([a_start, a_end, b_start, b_end]):
        return JsonResponse({"error": "需要提供 a_start, a_end, b_start, b_end 四個日期"}, status=400)

    try:
        data = get_graph_diff(a_start, a_end, b_start, b_end, industry)
        return JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def graph_diff_narrative_api(request):
    """
    接收前端 Diff 資料，呼叫 Gemini 生成變化解讀文字。
    Body: { nodes, links, filters: { a_start, a_end, b_start, b_end } }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    nodes   = body.get("nodes", [])
    links   = body.get("links", [])
    filters = body.get("filters", {})

    try:
        narrative = generate_graph_diff_narrative(nodes, links, filters)
        return JsonResponse({"narrative": narrative})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def generate_narrative_graph(request):
    """
    觸發敘事推理圖譜生成（方向 B）。

    Body（擇一）：
    - { "summary_id": 123 }            → 單筆
    - { "batch": true, "limit": 10 }   → 批次（limit 可省略）
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    summary_id = body.get("summary_id")
    is_batch   = body.get("batch", False)

    try:
        if summary_id:
            results = process_one_narrative(int(summary_id))
            return JsonResponse({
                "message": f"敘事圖生成完成（摘要 #{summary_id}）",
                "results": results,
            })
        elif is_batch:
            limit = body.get("limit")
            stats = process_all_narrative(limit=int(limit) if limit else None)
            return JsonResponse({"message": "批次敘事圖生成完成", "stats": stats})
        else:
            return JsonResponse({"error": "需提供 summary_id 或 batch: true"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def claim_analysis_page(request):
    """渲染主張分析頁面。"""
    return render(request, "knowledge_graph/claim_analysis.html")


def episodes_api(request):
    """回傳有 arguments 的摘要清單，供前端主張分析頁選集。"""
    try:
        limit = int(request.GET.get("limit", 100))
        summaries = list_summaries_with_arguments(limit=limit)
        return JsonResponse({"episodes": summaries})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def analyze_episode_api(request):
    """
    分析指定摘要的推理鏈（使用 SummaryRecord.arguments + 逐字稿）。
    Body: { "episode_id": 123 }   ← episode_id 實為 summary_id，保持前端相容
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    summary_id = body.get("episode_id")
    if not summary_id:
        return JsonResponse({"error": "需提供 episode_id（summary_id）"}, status=400)

    try:
        result = analyze_from_summary(int(summary_id))
        # 統一回傳格式：record → episode，claims 保持原樣
        return JsonResponse(
            {"episode": result.get("record"), "claims": result.get("claims", []), "error": result.get("error")},
            json_dumps_params={"ensure_ascii": False},
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def generate_graph(request):
    """手動傳入單筆摘要 JSON，觸發知識圖譜生成。"""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    summary_data = body.get("summary_data")
    podcast_source = body.get("podcast_source", "unknown")
    summary_date = body.get("summary_date", "2026-01-01")

    if not summary_data:
        return JsonResponse({"error": "summary_data is required"}, status=400)

    try:
        summary_text = json.dumps(summary_data, ensure_ascii=False)
        process_new_podcast(
            summary_text=summary_text,
            summary_date=summary_date,
            podcast_source=podcast_source,
        )
        return JsonResponse({"message": "知識圖譜生成成功", "source": podcast_source})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def leiden_cluster_page(request):
    """渲染 Leiden 兩期比較頁面。"""
    return render(request, "knowledge_graph/leiden_cluster.html")


@csrf_exempt
def leiden_cluster_api(request):
    """
    兩期 Leiden 社群偵測比較。

    Query params:
      - a_start, a_end   : 期間 A（YYYY-MM-DD）
      - b_start, b_end   : 期間 B（YYYY-MM-DD）
      - relation_type    : 可選
      - industry         : 可選
      - resolution       : float，預設 1.0
      - min_size         : int，預設 3
    """
    a_start       = request.GET.get("a_start",       "").strip() or None
    a_end         = request.GET.get("a_end",         "").strip() or None
    b_start       = request.GET.get("b_start",       "").strip() or None
    b_end         = request.GET.get("b_end",         "").strip() or None
    relation_type = request.GET.get("relation_type", "").strip() or None
    industry      = request.GET.get("industry",      "").strip() or None
    resolution    = float(request.GET.get("resolution", "1.0"))
    min_size      = int(request.GET.get("min_size", "3"))

    if not all([a_start, a_end, b_start, b_end]):
        return JsonResponse({"error": "需提供 a_start, a_end, b_start, b_end"}, status=400)

    try:
        from .services.leiden_cluster import fetch_links, run_leiden_diff
        links_a = fetch_links(a_start, a_end, relation_type, industry)
        links_b = fetch_links(b_start, b_end, relation_type, industry)
        result  = run_leiden_diff(links_a, links_b, resolution=resolution, min_community_size=min_size)
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def name_clusters_api(request):
    """
    對前端送來的 clusters 用 LLM 生成主題名稱。
    Body: { "clusters": [{"cluster_id": 0, "sample_reasons": ["...", "..."]}] }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    clusters = body.get("clusters", [])
    if not clusters:
        return JsonResponse({"results": []})

    try:
        from .services.narrative_cluster import name_clusters_with_llm
        from .filter import _get_client
        client     = _get_client()
        model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")
        enriched   = [{**c, "is_noise": False} for c in clusters]
        named      = name_clusters_with_llm(enriched, client, model_name)
        return JsonResponse({
            "results": [
                {"cluster_id": c["cluster_id"], "theme_label": c.get("theme_label", "")}
                for c in named
            ]
        }, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def narrative_cluster_diff_page(request):
    """渲染敘事群聚兩期比較頁面。"""
    return render(request, "knowledge_graph/narrative_cluster_diff.html")


@csrf_exempt
def narrative_cluster_diff_api(request):
    """
    比較兩段時間的敘事群聚差異。

    Query params:
      - a_start, a_end   : Period A 日期（YYYY-MM-DD）
      - b_start, b_end   : Period B 日期（YYYY-MM-DD）
      - relation_type    : Supply / Co-impact / Substitution（必填）
      - industry         : 產業關鍵字（可選）
    """
    a_start       = request.GET.get("a_start",       "").strip() or None
    a_end         = request.GET.get("a_end",         "").strip() or None
    b_start       = request.GET.get("b_start",       "").strip() or None
    b_end         = request.GET.get("b_end",         "").strip() or None
    relation_type = request.GET.get("relation_type", "").strip() or None
    industry      = request.GET.get("industry",      "").strip() or None

    if not relation_type:
        return JsonResponse({"error": "relation_type 為必填"}, status=400)
    if not all([a_start, a_end, b_start, b_end]):
        return JsonResponse({"error": "需提供 a_start, a_end, b_start, b_end"}, status=400)

    try:
        from .services.narrative_cluster import (
            fetch_links_for_cluster,
            run_cluster_diff,
        )
        links_a = fetch_links_for_cluster(a_start, a_end, relation_type, industry)
        links_b = fetch_links_for_cluster(b_start, b_end, relation_type, industry)
        result  = run_cluster_diff(links_a, links_b)
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def narrative_cluster_page(request):
    """渲染敘事群聚分析頁面。"""
    return render(request, "knowledge_graph/narrative_cluster.html")


@csrf_exempt
def narrative_cluster_api(request):
    """
    對指定條件的 links.reason 做 bge-m3 embedding + HDBSCAN 分群。

    Query params:
      - relation_type  (str, 必填): Supply / Co-impact / Substitution
      - start_date     (str, optional): YYYY-MM-DD
      - end_date       (str, optional): YYYY-MM-DD
      - industry       (str, optional): 產業關鍵字
      - label_with_llm (bool, optional): 是否用 LLM 命名，預設 false
    """
    relation_type  = request.GET.get("relation_type",  "").strip() or None
    start_date     = request.GET.get("start_date",     "").strip() or None
    end_date       = request.GET.get("end_date",       "").strip() or None
    industry       = request.GET.get("industry",       "").strip() or None
    label_with_llm = request.GET.get("label_with_llm", "false").lower() == "true"

    if not relation_type:
        return JsonResponse({"error": "relation_type 為必填（Supply / Co-impact / Substitution）"}, status=400)

    try:
        from .services.narrative_cluster import (
            fetch_links_for_cluster,
            run_reason_clustering,
            name_clusters_with_llm,
        )

        links = fetch_links_for_cluster(start_date, end_date, relation_type, industry)

        if not links:
            return JsonResponse({
                "clusters": [],
                "meta": {
                    "total_links":  0,
                    "num_clusters": 0,
                    "noise_links":  0,
                    "relation_type": relation_type,
                    "message": "查無符合條件的資料",
                },
            })

        clusters = run_reason_clustering(links)

        if label_with_llm:
            client     = _get_client()
            model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")
            clusters   = name_clusters_with_llm(clusters, client, model_name)

        noise_links = sum(c["size"] for c in clusters if c["is_noise"])

        return JsonResponse(
            {
                "clusters": clusters,
                "meta": {
                    "total_links":  len(links),
                    "num_clusters": sum(1 for c in clusters if not c["is_noise"]),
                    "noise_links":  noise_links,
                    "relation_type": relation_type,
                },
            },
            json_dumps_params={"ensure_ascii": False},
        )

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def generate_graph_from_summaries(request):
    """從 summariesdb 讀取所有摘要，批次生成知識圖譜。"""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        process_all_summaries()
        return JsonResponse({"message": "批次知識圖譜生成完成"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
