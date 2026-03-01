from django.shortcuts import render

# Create your views here.
import os
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import GenerateSummarySerializer
from .services.engine import summarize_from_srt_text

class GenerateSummaryView(APIView):
    def post(self, request):
        ser = GenerateSummarySerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        if not api_key:
            return Response({"error": "GEMINI_API_KEY not set"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        mode = ser.validated_data["mode"]
        srt_text = ser.validated_data["srt_text"]
        model = ser.validated_data["model"]
        chunk_threshold_chars = ser.validated_data["chunk_threshold_chars"]

        result = summarize_from_srt_text(
            api_key=api_key,
            srt_text=srt_text,
            mode=mode,
            model=model,
            chunk_threshold_chars=chunk_threshold_chars,
        )
        return Response(result, status=status.HTTP_200_OK)