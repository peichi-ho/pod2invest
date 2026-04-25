#api層
from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from apps.glossary.models import GlossaryTerm, GlossaryAlias
from apps.glossary.services.annotator import annotate
from django.db.models import Q, Case, When, IntegerField, Value
from rest_framework import status


#搜尋 term + alias，分兩段查詢避免 JOIN+DISTINCT+ORDER BY 在 PostgreSQL 的衝突
@api_view(["GET"])
def lookup(request):
    q = (request.GET.get("q") or "").strip()
    if not q:
        return Response({"error": "q is required"}, status=400)

    try:
        # 第一段：直接搜尋 term 欄位
        by_term = list(
            GlossaryTerm.objects
            .filter(is_active=True)
            .filter(Q(term__iexact=q) | Q(term__istartswith=q) | Q(term__icontains=q))
            .annotate(
                score=Case(
                    When(term__iexact=q,      then=Value(100)),
                    When(term__istartswith=q, then=Value(80)),
                    When(term__icontains=q,   then=Value(60)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            )
            .order_by("-score", "term")[:20]
        )

        # 第二段：搜尋 alias
        by_term_ids = [t.id for t in by_term]
        alias_term_ids = list(
            GlossaryAlias.objects
            .filter(Q(alias__iexact=q) | Q(alias__istartswith=q) | Q(alias__icontains=q))
            .values_list("term_id", flat=True)
        )
        by_alias = list(
            GlossaryTerm.objects
            .filter(is_active=True, id__in=alias_term_ids)
            .exclude(id__in=by_term_ids)[:10]
        ) if alias_term_ids else []

        results = (by_term + by_alias)[:20]

        data = []
        for t in results:
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

    except Exception as exc:
        import traceback
        return Response({"error": str(exc), "trace": traceback.format_exc(), "results": []}, status=500)


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
