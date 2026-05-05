import logging

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import SummarizeRequestSerializer, GenerateFromPodcastSerializer
from .services.engine import summarize_from_srt_text
from .models import SummaryRecord, BacktestingRecord
from apps.podcasts.models import PodcastEpisode
from apps.podcasts.models import PodcastEpisode
from apps.podcasts.models import PodcastEpisode
from apps.glossary.services.annotator import annotate
from apps.mindmap.services.gemini_mindmap import generate_mindmap_json

logger = logging.getLogger(__name__)


def _build_enrichments(result: dict) -> tuple:
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
        mind_map = generate_mindmap_json(result)
    except Exception:
        logger.exception("Mind map generation failed")

    return glossary_matches, mind_map


def _save_summary(result: dict, *, mode: str, model_name: str,
                  source_filename: str = "", podcaster: str = "",
                  published_at=None) -> SummaryRecord:
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

    glossary_matches, mind_map = _build_enrichments(enrich_base)

    record = SummaryRecord.objects.using("summariesdb").create(
        mode=mode,
        model=model_name,
        source_filename=source_filename,
        podcaster=podcaster,
        published_at=published_at,
        one_sentence_summary=one_sentence,
        investment_takeaways=investment_takeaways,
        tags=tags,
        entities=entities,
        arguments=arguments,
        glossary_matches=glossary_matches,
        mind_map=mind_map,
    )

    if outlook_calls:
        BacktestingRecord.objects.using("summariesdb").create(
            summary=record,
            outlook_calls=outlook_calls,
        )

    return record


class PodcastersRankingAPIView(APIView):
    def get(self, request):
        limit = int(request.query_params.get("limit", 10))
        from django.db import connections
        with connections["summariesdb"].cursor() as c:
            c.execute("""
                SELECT podcaster, COUNT(DISTINCT source_filename) as episodes, MAX(created_at) as latest
                FROM summaries_summaryrecord
                WHERE podcaster IS NOT NULL AND podcaster != ''
                GROUP BY podcaster
                ORDER BY episodes DESC, latest DESC
                LIMIT %s
            """, [limit])
            rows = c.fetchall()
        result = [{"podcaster": r[0], "episodes": r[1], "latest": r[2].isoformat()} for r in rows]
        return Response(result)


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
                model_name=data.get("model", "models/gemini-2.5-flash-lite"),
                source_filename=source_filename,
            )

            return Response(result | {"summary_id": record.id}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"detail": "摘要失敗", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SummaryListAPIView(APIView):
    def get(self, request):
        source_filename = request.query_params.get("source_filename")
        podcaster = request.query_params.get("podcaster")
        mode = request.query_params.get("mode")

        qs = SummaryRecord.objects.using("summariesdb").order_by("-created_at")
        if source_filename:
            qs = qs.filter(source_filename=source_filename)
        if podcaster:
            qs = qs.filter(podcaster=podcaster)
        if mode:
            qs = qs.filter(mode=mode)
        # Apply limit only when not filtering by a specific episode or podcaster
        if not source_filename and not podcaster:
            limit = int(request.query_params.get("limit", 20))
            qs = qs[:limit]
        elif request.query_params.get("limit"):
            qs = qs[:int(request.query_params.get("limit"))]

        result = []
        for s in qs:
            backtesting = BacktestingRecord.objects.using("summariesdb").filter(summary_id=s.id).first()
            outlook_calls = backtesting.outlook_calls if backtesting else []
            result.append({
                "id": s.id,
                "mode": s.mode,
                "source_filename": s.source_filename,
                "podcaster": s.podcaster,
                "one_sentence_summary": s.one_sentence_summary,
                "tags": s.tags,
                "entities": s.entities,
                "created_at": s.created_at.isoformat(),
                "outlook_calls": outlook_calls,
            })
        return Response(result)


class SummaryDetailAPIView(APIView):
    def get(self, request, pk):
        try:
            s = SummaryRecord.objects.using("summariesdb").get(pk=pk)
        except SummaryRecord.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)

        # 抓同一集（相同 source_filename）所有摘要的 outlook_calls，合併去重
        if s.source_filename:
            related_ids = SummaryRecord.objects.using("summariesdb").filter(
                source_filename=s.source_filename
            ).values_list("id", flat=True)
            bt_records = BacktestingRecord.objects.using("summariesdb").filter(
                summary_id__in=related_ids
            )
        else:
            bt_records = BacktestingRecord.objects.using("summariesdb").filter(summary_id=s.id)

        seen_theses = set()
        outlook_calls = []
        for bt in bt_records:
            for call in (bt.outlook_calls or []):
                thesis = call.get("thesis", "")
                if thesis and thesis not in seen_theses:
                    seen_theses.add(thesis)
                    outlook_calls.append(call)

        audio_url = ""
        if s.source_filename:
            try:
                episode = PodcastEpisode.objects.using("podcasts").filter(
                    episode_title=s.source_filename
                ).first()
                if episode:
                    audio_url = episode.audio_url
            except Exception:
                logger.exception("Failed to fetch audio_url from podcasts DB")

        return Response({
            "id": s.id,
            "source_filename": s.source_filename,
            "podcaster": s.podcaster,
            "mode": s.mode,
            "one_sentence_summary": s.one_sentence_summary,
            "investment_takeaways": s.investment_takeaways,
            "tags": s.tags,
            "entities": s.entities,
            "arguments": s.arguments,
            "outlook_calls": outlook_calls,
            "audio_url": audio_url,
            "published_at": s.published_at.isoformat() if s.published_at else None,
            "created_at": s.created_at.isoformat(),
        })


class SummaryMindmapAPIView(APIView):
    def get(self, request, pk):
        try:
            s = SummaryRecord.objects.using("summariesdb").get(pk=pk)
        except SummaryRecord.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response({"mindmap": s.mind_map})


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
            episode = PodcastEpisode.objects.using("podcasts").select_related('podcast', 'transcript').get(id=podcast_id)
        except PodcastEpisode.DoesNotExist:
            episode = PodcastEpisode.objects.using("podcasts").select_related('podcast', 'transcript').get(id=podcast_id)
        except PodcastEpisode.DoesNotExist:
            return Response(
                {"detail": f"找不到 podcast id={podcast_id}"},
                status=status.HTTP_404_NOT_FOUND,
            )

        srt_content = getattr(getattr(episode, 'transcript', None), 'srt_content', None)
        if not srt_content:
        srt_content = getattr(getattr(episode, 'transcript', None), 'srt_content', None)
        if not srt_content:
            return Response(
                {"detail": f"podcast id={podcast_id} 沒有 srt_content，請先完成轉錄。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = summarize_from_srt_text(
                api_key=api_key,
                srt_text=srt_content,
                srt_text=srt_content,
                mode=data["mode"],
                model=data.get("model", "models/gemini-2.5-flash-lite"),
                chunk_threshold_chars=data.get("chunk_threshold_chars", 30000),
                debug_chars=0,
            )

            record = _save_summary(
                result,
                mode=data["mode"],
                model_name=data.get("model", "models/gemini-2.5-flash-lite"),
                source_filename=episode.episode_title,
                podcaster=episode.podcast.show_name,
                published_at=episode.published_at,
                source_filename=episode.episode_title,
                podcaster=episode.podcast.show_name,
                published_at=episode.published_at,
            )

            return Response(result | {"summary_id": record.id}, status=status.HTTP_200_OK)



        except Exception as e:
            return Response(
                {"detail": "摘要失敗", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
