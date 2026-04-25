import json
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from django.db import connections

from .generate import process_new_podcast, process_all_summaries
from .filter import get_graph_data, generate_graph_narrative


def graph_page(request):
    """渲染知識圖譜互動頁面。"""
    return render(request, "knowledge_graph/graph.html")


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
