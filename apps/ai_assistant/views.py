from django.shortcuts import render

import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .services.llm import answer_user

@csrf_exempt
@require_POST
def chat(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    query = (payload.get("query") or "").strip()
    if not query:
        return JsonResponse({"ok": False, "error": "Missing query"}, status=400)

    try:
        answer = answer_user(query)
        return JsonResponse({"ok": True, "answer": answer})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)