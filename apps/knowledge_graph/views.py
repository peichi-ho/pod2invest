import json
from collections import defaultdict
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from django.db import connections

from .generate import process_new_podcast, process_all_summaries
from .generate_by_podcast import process_new_episode, process_all_episodes
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


def _source_candidates(source: str) -> set:
    """
    links.podcast_source 可能是「集數標題」（跟 SummaryRecord.source_filename 一樣，
    process_all_summaries 就是這樣寫入），也可能是「節目名 - 集數標題」（目前資料庫裡大部分是這種）。
    回傳這個來源所有可能的集數標題寫法：完整字串，以及每個「 - 」之後的剩餘字串
    （集數標題本身也可能含有「 - 」，所以每個位置都試）。
    """
    candidates = {source}
    start = 0
    while True:
        i = source.find(" - ", start)
        if i == -1:
            break
        candidates.add(source[i + 3:])
        start = i + 1
    return candidates


def resolve_sources_to_summaries(sources) -> dict:
    """
    把 links.podcast_source 對回 summariesdb 的 SummaryRecord。
    回傳 {podcast_source: {"id", "source_filename"}}，每個來源（＝每一集）只挑一筆，優先新手版；
    摘要庫裡沒有這一集的來源不會出現在結果裡。
    「今日市場焦點」卡片上寫的集數、點進去列出的集數都走這支，兩邊才會一致。
    """
    from apps.summaries.models import SummaryRecord

    sources = [s for s in set(sources) if s]
    if not sources:
        return {}
    candidates_by_source = {s: _source_candidates(s) for s in sources}
    all_candidates = set().union(*candidates_by_source.values())

    by_filename = {}
    rows = (SummaryRecord.objects.using("summariesdb")
            .filter(source_filename__in=all_candidates)
            .values("id", "source_filename", "mode"))
    for r in rows:
        current = by_filename.get(r["source_filename"])
        if current is None or (r["mode"] == "novice" and current["mode"] != "novice"):
            by_filename[r["source_filename"]] = r

    resolved = {}
    for source, candidates in candidates_by_source.items():
        for candidate in sorted(candidates, key=len, reverse=True):  # 優先用最完整的寫法比對
            if candidate in by_filename:
                resolved[source] = by_filename[candidate]
                break
    return resolved


def node_episodes_api(request):
    """
    回傳某節點在最近 N 天內出現過的 podcast_source 清單，以及其中摘要庫真的有的集數
    （summary_ids，每集一筆）。前端用 summary_ids 直接列出集數。
    """
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
        resolved = resolve_sources_to_summaries(sources)
        episodes = {r["source_filename"]: r["id"] for r in resolved.values()}
        return JsonResponse({"sources": sources, "node": node, "summary_ids": sorted(episodes.values())})
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
    """
    回傳最近 N 天內最熱門的節點。
    count    ：該節點在 links 裡的關係邊筆數（source+target 合計）
    episodes ：這些關係來自幾集「摘要庫真的有的」節目——前端卡片寫的「N 集提及」就是這個數字，
               點進去列出的集數跟它一致（見 node_episodes_api）。摘要庫裡一集都對不到的節點不會出現，
               不然卡片寫有討論、點進去卻是空的。依 episodes、count 由大到小排序。
    """
    days = int(request.GET.get("days", 30))
    limit = int(request.GET.get("limit", 12))
    # 先多抓一些候選，扣掉對不到摘要的之後才湊得滿 limit
    candidate_limit = min(limit * 4, 80)
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
            """, [days, days, candidate_limit])
            candidates = [(r[0], int(r[1])) for r in cursor.fetchall()]

            sources_by_node = defaultdict(set)
            if candidates:
                names = [c[0] for c in candidates]
                cursor.execute("""
                    SELECT name, podcast_source FROM (
                        SELECT source AS name, podcast_source FROM links
                        WHERE summary_date >= NOW() - INTERVAL '%s days' AND source = ANY(%s)
                        UNION
                        SELECT target AS name, podcast_source FROM links
                        WHERE summary_date >= NOW() - INTERVAL '%s days' AND target = ANY(%s)
                    ) t WHERE podcast_source IS NOT NULL AND podcast_source <> ''
                """, [days, names, days, names])
                for name, source in cursor.fetchall():
                    sources_by_node[name].add(source)

        resolved = resolve_sources_to_summaries(
            [src for srcs in sources_by_node.values() for src in srcs]
        )
        nodes = []
        for name, count in candidates:
            episodes = {resolved[s]["source_filename"] for s in sources_by_node[name] if s in resolved}
            if episodes:
                nodes.append({"name": name, "count": count, "episodes": len(episodes)})
        nodes.sort(key=lambda n: (-n["episodes"], -n["count"]))  # 集數多的在前，同樣集數再比關係邊筆數
        return JsonResponse({"nodes": nodes[:limit], "days": days})
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


# ==========================================
# Basket（投資組合探索）
# ==========================================

def basket_page(request):
    """渲染 Basket 探索頁面。"""
    from .services.seed_suggest import warm_async
    warm_async()    # 先在背景建好「查詢標的」搜尋建議的索引
    return render(request, "knowledge_graph/basket.html")


def seed_suggest_api(request):
    """探索頁「查詢標的」輸入框的搜尋建議：名稱或代號符合輸入文字的投資標的節點。"""
    from .services.seed_suggest import suggest_seeds
    q = request.GET.get("q", "").strip()
    return JsonResponse({"suggestions": suggest_seeds(q)}, json_dumps_params={"ensure_ascii": False})


def basket_purposes_api(request):
    """回傳可選的投資目的清單，供前端下拉選單使用。"""
    from .services.basket_purpose import PURPOSE_CHOICES
    return JsonResponse({"purposes": PURPOSE_CHOICES}, json_dumps_params={"ensure_ascii": False})


def _parse_basket_params(request):
    """
    共用參數解析：成功時回傳 (params_dict, None)，失敗時回傳 (None, JsonResponse)。
    basket_api 和 basket_stream_api 共用同一組參數規則，避免兩邊各自驗證、
    之後改規則只改到其中一邊而悄悄不一致。
    """
    import datetime as _dt
    from .services.basket_purpose import get_strategy_for_purpose

    seed = request.GET.get("seed", "").strip()
    purpose = request.GET.get("purpose", "").strip()
    end_date_str = request.GET.get("end_date", "").strip()
    window_days = int(request.GET.get("window_days", "30"))
    risk_tier = request.GET.get("risk_tier", "").strip() or None

    if not seed or not purpose:
        return None, JsonResponse({"error": "需提供 seed 與 purpose"}, status=400)

    if risk_tier and risk_tier not in ("保守", "均衡", "積極"):
        return None, JsonResponse({"error": "risk_tier 須為 保守/均衡/積極 其中之一"}, status=400)

    strategy = get_strategy_for_purpose(purpose)
    if not strategy:
        return None, JsonResponse({"error": f"未知的 purpose: {purpose}"}, status=400)

    try:
        end_date = (
            _dt.datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if end_date_str else _dt.date.today()
        )
    except ValueError:
        return None, JsonResponse({"error": "end_date 格式須為 YYYY-MM-DD"}, status=400)

    return {
        "seed": seed, "purpose": purpose, "strategy": strategy,
        "end_date": end_date, "window_days": window_days, "risk_tier": risk_tier,
    }, None


def _run_basket_search(params, on_progress=None):
    """
    實際跑候選產生 + Selection pipeline，回傳跟 basket_api 回應同樣格式的 dict。
    basket_api（一次性回傳）和 basket_stream_api（SSE 即時進度）共用這個函式，
    差別只在有沒有傳 on_progress。
    """
    from .services.strategy_supply import supply_candidates
    from .services.strategy_substitute import substitute_candidates
    from .services.strategy_coimpact import co_impact_candidates
    from .services.basket_selection import select_basket, seed_exists, suggest_similar_seeds

    seed, strategy = params["seed"], params["strategy"]
    end_date, window_days, risk_tier = params["end_date"], params["window_days"], params["risk_tier"]

    if strategy in ("supply_upstream", "supply_downstream"):
        result = supply_candidates(seed, end_date, window_days)
        direction = "upstream" if strategy == "supply_upstream" else "downstream"
        candidates = result[direction]
    elif strategy == "substitute":
        candidates = substitute_candidates(seed, end_date, window_days)
    elif strategy == "co_impact":
        candidates = co_impact_candidates(seed, end_date, window_days)
    else:
        candidates = []

    selection = select_basket(
        candidates, strategy=strategy, seed=seed, window_days=window_days,
        risk_tier=risk_tier, as_of_date=end_date, on_progress=on_progress,
    )

    seed_suggestions = []
    if not candidates and not seed_exists(seed):
        seed_suggestions = suggest_similar_seeds(seed)

    return {
        "seed": seed,
        "purpose": params["purpose"],
        "strategy": strategy,
        "window_days": window_days,
        "end_date": end_date.isoformat(),
        "risk_tier": risk_tier,
        "candidate_count": len(candidates),
        "basket": selection["basket"],
        "funnel": selection["funnel"],
        "seed_suggestions": seed_suggestions,
    }


@csrf_exempt
def basket_api(request):
    """
    輸入 seed（標的名稱）+ purpose（投資目的 key），回傳對應策略算出的 basket。
    一次性回傳完整結果；查詢過程中的即時進度見 basket_stream_api。

    Query params:
      - seed        : 必填，標的名稱（需與知識圖譜節點名稱一致）
      - purpose     : 必填，見 basket_purpose.PURPOSE_CHOICES 的 key
      - end_date    : 選填，YYYY-MM-DD，預設今天
      - window_days : 選填，預設 30
    """
    params, err = _parse_basket_params(request)
    if err:
        return err
    try:
        result = _run_basket_search(params)
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def basket_stream_api(request):
    """
    basket_api 的 SSE 版本：查詢通常要跑好幾個 LLM 判斷 + 逐檔財務資料 API，
    可能要等 30 秒以上，前端需要能顯示「現在跑到哪一步、還要等多久」而不是
    乾等一個轉圈圈。

    做法：查詢邏輯丟到背景 thread 跑，用 queue.Queue 當橋接——select_basket()
    內部呼叫 on_progress 時把訊息放進 queue，這裡的 generator 在主 thread
    不斷從 queue 取出訊息轉成 SSE 格式吐給前端，直到收到 done/error 為止。
    沒有另外引入 Celery/Redis 之類的排隊系統，單一查詢的量體不需要。

    Query params 跟 basket_api 相同。回應格式（text/event-stream，每則一個
    JSON object）：
      - {"type": "progress", "stage", "label", "percent", "item_current", "item_total"}
      - {"type": "done", "result": {...跟 basket_api 回應格式相同...}}
      - {"type": "error", "error": "..."}
    """
    import json as _json
    import queue
    import threading
    from django.http import StreamingHttpResponse

    params, err = _parse_basket_params(request)
    if err:
        return err

    q: queue.Queue = queue.Queue()

    def emit(**kwargs):
        q.put({"type": "progress", **kwargs})

    def worker():
        try:
            result = _run_basket_search(params, on_progress=emit)
            q.put({"type": "done", "result": result})
        except Exception as e:
            q.put({"type": "error", "error": str(e)})

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        while True:
            msg = q.get()
            yield f"data: {_json.dumps(msg, ensure_ascii=False)}\n\n"
            if msg["type"] in ("done", "error"):
                break

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def asset_scores_api(request):
    """
    Basket 卡片上的「Podcast 看法」分數（見 services/asset_kg_score.py）。
    Basket 結果出來後由前端另外呼叫，不拖慢原本的搜尋。

    Query params:
      - node        : 必填，可重複，知識圖譜節點名稱（最多 20 個）
      - end_date    : 選填，YYYY-MM-DD，預設今天
      - window_days : 選填，預設 30
    """
    import datetime as _dt
    from .services.asset_kg_score import compute_asset_scores

    nodes = list(dict.fromkeys(n.strip() for n in request.GET.getlist("node") if n.strip()))
    if not nodes:
        return JsonResponse({"error": "需提供 node"}, status=400)
    if len(nodes) > 20:
        return JsonResponse({"error": "node 最多 20 個"}, status=400)
    try:
        window_days = int(request.GET.get("window_days", "30"))
    except ValueError:
        return JsonResponse({"error": "window_days 須為整數"}, status=400)
    if not 1 <= window_days <= 365:
        return JsonResponse({"error": "window_days 須介於 1~365"}, status=400)
    end_date_str = request.GET.get("end_date", "").strip()
    try:
        end_date = (
            _dt.datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if end_date_str else _dt.date.today()
        )
    except ValueError:
        return JsonResponse({"error": "end_date 格式須為 YYYY-MM-DD"}, status=400)

    try:
        scores = compute_asset_scores(nodes, end_date, window_days)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    return JsonResponse(
        {"end_date": end_date.isoformat(), "window_days": window_days, "scores": scores},
        json_dumps_params={"ensure_ascii": False},
    )


@csrf_exempt
def generate_graph_from_episode(request):
    """
    以逐字稿為來源，觸發單一 PodcastEpisode 的知識圖譜生成
    （generate_by_podcast.py：三步驟抽取 + 規則過濾）。
    Body: { "episode_id": 123 }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    episode_id = body.get("episode_id")
    if not episode_id:
        return JsonResponse({"error": "episode_id is required"}, status=400)

    try:
        process_new_episode(int(episode_id))
        return JsonResponse({"message": "知識圖譜生成成功", "episode_id": episode_id})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def generate_graph_from_episodes(request):
    """
    以逐字稿為來源，批次生成知識圖譜（generate_by_podcast.py）。
    Body（皆可選）：
    - limit             (int) : 限制處理筆數，供小範圍測試
    - published_after    (str) : YYYY-MM-DD，篩選 published_at 下限
    - published_before   (str) : YYYY-MM-DD，篩選 published_at 上限
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    limit = body.get("limit")

    try:
        process_all_episodes(
            limit=int(limit) if limit else None,
            published_after=body.get("published_after"),
            published_before=body.get("published_before"),
        )
        return JsonResponse({"message": "批次知識圖譜生成完成（逐字稿來源）"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
