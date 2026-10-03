#api層
from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from apps.glossary.models import GlossaryTerm
from apps.glossary.services.annotator import annotate
from rest_framework import status


#呼叫 annotate()，找出所有出現的詞 + 正確位置
#回傳 matches
#不修改原文，只回傳摘要裡標註資訊
#前端可以 hover 顯示解釋
#可視化標註專有名詞
@api_view(["POST"])
def annotate_api(request):
    text = (request.data.get("text") or "").strip()

    if not text:
        return Response({"error": "text is required"}, status=400)

    result = annotate(text)

    return Response(result)


#拿單一詞條的完整資料
#用於前端點擊展開詳情
@api_view(["GET"])
def term_detail(request, term_id: int):
    try:
        t = GlossaryTerm.objects.prefetch_related("aliases").get(id=term_id, is_active=True)
    except GlossaryTerm.DoesNotExist:
        return Response({"error": "term not found"}, status=status.HTTP_404_NOT_FOUND)

    return Response({
        "id": t.id,
        "term": t.term,
        "short_definition": t.short_definition,
        "long_definition": t.long_definition,
        "category": t.category,
        "lang": t.lang,
        "aliases": [a.alias for a in t.aliases.all()],
    })
