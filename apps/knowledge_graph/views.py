from django.shortcuts import render

# Create your views here.
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .generate import init_db, process_new_podcast

@csrf_exempt
def generate_graph(request):
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
        init_db()
        summary_text = json.dumps(summary_data, ensure_ascii=False)
        process_new_podcast(
            summary_text=summary_text,
            summary_date=summary_date,
            podcast_source=podcast_source
        )
        return JsonResponse({"message": "知識圖譜生成成功", "source": podcast_source})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
