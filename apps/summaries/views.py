import logging

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import SummarizeRequestSerializer, GenerateFromPodcastSerializer
from .services.engine import summarize_from_srt_text
from .services.backtesting import create_backtesting_rows
from .models import SummaryRecord
from apps.podcasts.models import PodcastEpisode
from apps.glossary.services.annotator import annotate
from apps.mindmap.services.gemini_mindmap import generate_mindmap_json

logger = logging.getLogger(__name__)


def _build_enrichments(result: dict, api_key: str = "") -> tuple:
    """
    Run glossary matching and mind map generation on a summary result dict.
    Returns (glossary_matches, mind_map). Both are non-fatal — failures return empty values.
    """
    # --- Glossary matching ---
    glossary_matches = []
    try:
        parts = []
        parts.append(result.get("one_sentence_summary") or "")

        takeaways = result.get("investment_takeaways") or {}
        for key in ("bullish", "bearish", "watchlist"):
            items = takeaways.get(key) or []
            if isinstance(items, list):
                parts.extend(items)

        for arg in result.get("arguments") or []:
            parts.append(arg.get("topic") or "")
            parts.append(arg.get("position") or "")
            summary = arg.get("summary") or ""
            if isinstance(summary, list):
                parts.extend(summary)
            else:
                parts.append(summary)

        combined_text = " ".join(p for p in parts if p)
        if combined_text:
            annotation = annotate(combined_text)
            glossary_matches = annotation.get("matches", [])
    except Exception:
        logger.exception("Glossary annotation failed")

    # --- Mind map ---
    mind_map = {}
    try:
        mind_map = generate_mindmap_json(result, api_key=api_key)
    except Exception:
        logger.exception("Mind map generation failed")

    return glossary_matches, mind_map


def _save_summary(result: dict, *, mode: str,
                  source_filename: str = "", podcaster: str = "",
                  published_at=None, episode_id=None,
                  api_key: str = "") -> SummaryRecord:
    """
    Build enrichments, save SummaryRecord + BacktestingRecord, and return the record.
    Handles both single-mode result (dict) and both-mode result ({"novice":..., "pro":...}).
    """
    # For both-mode, use the pro sub-result for enrichment; fall back to novice
    if mode == "both":
        enrich_base = result.get("pro") or result.get("novice") or {}
        one_sentence = enrich_base.get("one_sentence_summary", "")
        investment_takeaways = enrich_base.get("investment_takeaways", {})
        tags = enrich_base.get("tags", [])
        entities = enrich_base.get("entities", {})
        arguments = enrich_base.get("arguments", [])
        outlook_calls = enrich_base.get("outlook_calls", [])
    else:
        enrich_base = result
        one_sentence = result.get("one_sentence_summary", "")
        investment_takeaways = result.get("investment_takeaways", {})
        tags = result.get("tags", [])
        entities = result.get("entities", {})
        arguments = result.get("arguments", [])
        outlook_calls = result.get("outlook_calls", [])

    glossary_matches, mind_map = _build_enrichments(enrich_base, api_key=api_key)

    record = SummaryRecord.objects.using("summariesdb").create(
        mode=mode,
        source_filename=source_filename,
        podcaster=podcaster,
        published_at=published_at,
        episode_id=episode_id,
        one_sentence_summary=one_sentence,
        investment_takeaways=investment_takeaways,
        tags=tags,
        entities=entities,
        arguments=arguments,
        glossary_matches=glossary_matches,
        mind_map=mind_map,
        outlook_calls=outlook_calls,
    )

    # 建立回測列（backtesting.py 內部會過濾 null timeframe 與無法解析的 ticker）
    create_backtesting_rows(
        summary_record=record,
        episode_id=episode_id,
        outlook_calls=outlook_calls,
        published_at=published_at,
    )

    return record


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

            record = _save_summary(
                result,
                mode=data["mode"],
                source_filename=source_filename,
                api_key=api_key,
            )

            return Response(result | {"summary_id": record.id}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"detail": "摘要失敗", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GenerateFromPodcastAPIView(APIView):
    def post(self, request):
        serializer = GenerateFromPodcastSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        api_key = getattr(settings, "GEMINI_API_KEY", "").strip()
        if not api_key:
            return Response(
                {"detail": "GEMINI_API_KEY 未設定。"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        podcast_id = data["podcast_id"]
        try:
            episode = PodcastEpisode.objects.using("podcasts").select_related(
                "podcast", "transcript"
            ).get(id=podcast_id)
        except PodcastEpisode.DoesNotExist:
            return Response(
                {"detail": f"找不到 podcast id={podcast_id}"},
                status=status.HTTP_404_NOT_FOUND,
            )

        transcript = getattr(episode, "transcript", None)
        if not transcript or not transcript.srt_content:
            return Response(
                {"detail": f"podcast id={podcast_id} 沒有 srt_content，請先完成轉錄。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = summarize_from_srt_text(
                api_key=api_key,
                srt_text=transcript.srt_content,
                mode=data["mode"],
                model=data.get("model", "models/gemini-2.5-flash-lite"),
                chunk_threshold_chars=data.get("chunk_threshold_chars", 30000),
                debug_chars=0,
                published_at=episode.published_at,
            )

            record = _save_summary(
                result,
                mode=data["mode"],
                source_filename=episode.episode_title,
                podcaster=episode.podcast.show_name,
                published_at=episode.published_at,
                episode_id=episode.id,
                api_key=api_key,
            )

            return Response(result | {"summary_id": record.id}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"detail": "摘要失敗", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
