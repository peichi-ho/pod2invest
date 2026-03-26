from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import SummarizeRequestSerializer
from .services.engine import summarize_from_srt_text
from .models import SummaryRecord


class SummarizeAPIView(APIView):
    def post(self, request):
        serializer = SummarizeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        api_key = getattr(settings, "GEMINI_API_KEY", "").strip()
        if not api_key:
            return Response(
                {"detail": "GEMINI_API_KEY 未設定。"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        srt_text = data.get("srt_text", "")
        srt_file = data.get("srt_file")
        source_filename = ""

        if srt_file:
            source_filename = srt_file.name
            raw_bytes = srt_file.read()
            try:
                srt_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                srt_text = raw_bytes.decode("utf-8-sig", errors="ignore")

        try:
            result = summarize_from_srt_text(
                api_key=api_key,
                srt_text=srt_text,
                mode=data["mode"],
                model=data.get("model", "models/gemini-2.5-flash-lite"),
                chunk_threshold_chars=data.get("chunk_threshold_chars", 30000),
                debug_chars=data.get("debug_chars", 0),
            )

            SummaryRecord.objects.using("summariesdb").create(
                mode=data["mode"],
                model=data.get("model", "models/gemini-2.5-flash-lite"),
                source_filename=source_filename,
                one_sentence_summary=result.get("one_sentence_summary", ""),
                investment_takeaways=result.get("investment_takeaways", {}),
                tags=result.get("tags", []),
                entities=result.get("entities", {}),
                arguments=result.get("arguments", []),
                outlook_calls=result.get("outlook_calls", []),
            )

            return Response(result, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {
                    "detail": "摘要失敗",
                    "error": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )