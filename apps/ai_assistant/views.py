from django.shortcuts import render

import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.utils import timezone

from .models import Conversation, Message
from .services.ai_assistant import answer_user


# =============================================================================
# 日期分組輔助
# =============================================================================

def _get_date_group(dt) -> str:
    """將 datetime 分組為：今天 / 本週 / 本月 / 本季 / 更早"""
    now = timezone.now()

    if dt.date() == now.date():
        return "今天"

    # 本週（週一為起點）
    start_of_week = now - timezone.timedelta(days=now.weekday())
    if dt >= start_of_week.replace(hour=0, minute=0, second=0, microsecond=0):
        return "本週"

    # 本月
    if dt.year == now.year and dt.month == now.month:
        return "本月"

    # 本季
    current_quarter = (now.month - 1) // 3
    dt_quarter = (dt.month - 1) // 3
    if dt.year == now.year and dt_quarter == current_quarter:
        return "本季"

    return "更早"


# =============================================================================
# Chat endpoint
# =============================================================================

@csrf_exempt
@require_POST
def chat(request):
    """
    POST /ai-assistant/chat/
    Body: { "query": "...", "conversation_id": 123 }  (conversation_id 選填)
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    query = (payload.get("query") or "").strip()
    if not query:
        return JsonResponse({"ok": False, "error": "Missing query"}, status=400)

    conversation_id = payload.get("conversation_id")

    # 取得或建立 Conversation
    if conversation_id:
        try:
            conversation = Conversation.objects.get(pk=conversation_id)
        except Conversation.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Conversation not found"}, status=404)
    else:
        # 自動以問題前 30 字作為標題
        title = query[:30] + ("..." if len(query) > 30 else "")
        conversation = Conversation.objects.create(title=title)

    # 載入歷史訊息
    past_messages = conversation.messages.all()
    history = [{"role": msg.role, "content": msg.content} for msg in past_messages]

    # 呼叫 AI
    try:
        answer = answer_user(query, history)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

    # 儲存這輪的 user + assistant 訊息
    Message.objects.create(conversation=conversation, role="user", content=query)
    Message.objects.create(conversation=conversation, role="assistant", content=answer)

    return JsonResponse({
        "ok": True,
        "answer": answer,
        "conversation_id": conversation.pk,
    })


# =============================================================================
# Conversations list（按日期分組）
# =============================================================================

@csrf_exempt
@require_GET
def conversation_list(request):
    """
    GET /ai-assistant/conversations/
    回傳按日期分組的對話清單
    """
    conversations = Conversation.objects.all()

    groups: dict = {}
    for conv in conversations:
        group = _get_date_group(conv.created_at)
        if group not in groups:
            groups[group] = []
        groups[group].append({
            "id": conv.pk,
            "title": conv.title,
            "created_at": conv.created_at.isoformat(),
        })

    # 固定顯示順序
    order = ["今天", "本週", "本月", "本季", "更早"]
    result = [
        {"group": g, "conversations": groups[g]}
        for g in order if g in groups
    ]

    return JsonResponse({"ok": True, "data": result})


# =============================================================================
# Conversation messages
# =============================================================================

@csrf_exempt
@require_GET
def conversation_messages(request, conversation_id):
    """
    GET /ai-assistant/conversations/<id>/messages/
    回傳該對話的所有訊息
    """
    try:
        conversation = Conversation.objects.get(pk=conversation_id)
    except Conversation.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Conversation not found"}, status=404)

    messages = conversation.messages.all()
    data = [
        {
            "id": msg.pk,
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at.isoformat(),
        }
        for msg in messages
    ]

    return JsonResponse({"ok": True, "data": data})


# =============================================================================
# Delete conversation
# =============================================================================

@csrf_exempt
def delete_conversation(request, conversation_id):
    """
    DELETE /ai-assistant/conversations/<id>/
    """
    if request.method != "DELETE":
        return JsonResponse({"ok": False, "error": "Method not allowed"}, status=405)

    try:
        conversation = Conversation.objects.get(pk=conversation_id)
    except Conversation.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Conversation not found"}, status=404)

    conversation.delete()
    return JsonResponse({"ok": True})
