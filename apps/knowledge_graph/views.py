import json
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from django.db import connections

from .generate import process_new_podcast, process_all_summaries
from .filter import (
    get_graph_data, generate_graph_narrative, get_latest_date_in_db,
    get_graph_diff, generate_graph_diff_narrative,
)
from .generate_narrative import (
    process_one_narrative, process_all_narrative,
    list_summaries_with_arguments, analyze_from_summary, test_one_episode,
)


def graph_page(request):
    """渲染知識圖譜互動頁面。"""
    return render(request, "knowledge_graph/graph.html")


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
