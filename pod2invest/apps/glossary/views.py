#api層
from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from apps.glossary.models import GlossaryTerm
from apps.glossary.services.annotator import annotate
from django.db.models import Q, Case, When, IntegerField, Value
from rest_framework import status


#搜尋 term + alias
#有 relevance score
#term 完全相等 > 前綴 > 包含 > alias
#最多回傳 20 筆
@api_view(["GET"])
def lookup(request):
    q = (request.GET.get("q") or "").strip()
    if not q:
        return Response({"error": "q is required"}, status=400)

    terms = (
        GlossaryTerm.objects
        .filter(is_active=True)
        .filter(Q(term__icontains=q) | Q(aliases__alias__icontains=q))
        .annotate(
            score=Case(
                # term 優先
                When(term__iexact=q, then=Value(100)),          # term 完全相等
                When(term__istartswith=q, then=Value(80)),      # term 前綴
                When(term__icontains=q, then=Value(60)),        # term 包含

                # alias 次之
                When(aliases__alias__iexact=q, then=Value(40)),      # alias 完全相等
                When(aliases__alias__istartswith=q, then=Value(20)), # alias 前綴
                When(aliases__alias__icontains=q, then=Value(10)),   # alias 包含

                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by("-score", "term")  # 分數高的先出來；同分再按字母
        .distinct()
    )
    terms = terms[:20]
    data = []
    for t in terms:
        data.append({
            "id": t.id,
            "term": t.term,
            "short_definition": t.short_definition,
            "long_definition": t.long_definition,
            "category": t.category,
            "lang": t.lang,
            "aliases": [a.alias for a in t.aliases.all()],
        })
    return Response({"results": data})


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
