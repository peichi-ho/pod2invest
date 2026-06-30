import logging

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import SummarizeRequestSerializer, GenerateFromPodcastSerializer
from .services.engine import summarize_from_srt_text
from .services.backtesting import create_backtesting_rows
from .models import SummaryRecord, BacktestingRecord, TickerMap, SpeakerAccuracy
from apps.podcasts.models import PodcastEpisode
from apps.glossary.services.annotator import annotate
from apps.mindmap.services.gemini_mindmap import generate_mindmap_json

logger = logging.getLogger(__name__)


def _build_enrichments(result: dict, api_key: str = "") -> tuple:
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

    mind_map = {}
    try:
        mind_map = generate_mindmap_json(result, api_key=api_key)
    except Exception:
        logger.exception("Mind map generation failed")

    return glossary_matches, mind_map


def _create_single_record(data: dict, *, mode: str,
                           source_filename: str = "", podcaster: str = "",
                           published_at=None, episode_id=None,
                           api_key: str = "") -> SummaryRecord:
    one_sentence = data.get("one_sentence_summary", "")
    investment_takeaways = data.get("investment_takeaways", {})
    tags = data.get("tags", [])
    entities = data.get("entities", {})
    arguments = data.get("arguments", [])
    outlook_calls = data.get("outlook_calls", [])
    glossary_matches, mind_map = _build_enrichments(data, api_key=api_key)
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
    create_backtesting_rows(
        summary_record=record,
        episode_id=episode_id,
        outlook_calls=outlook_calls,
        published_at=published_at,
    )
    return record


def _save_summary(result: dict, *, mode: str,
                  source_filename: str = "", podcaster: str = "",
                  published_at=None, episode_id=None,
                  api_key: str = "") -> SummaryRecord:
    kwargs = dict(source_filename=source_filename, podcaster=podcaster,
                  published_at=published_at, episode_id=episode_id, api_key=api_key)
    if mode == "both":
        novice_record = _create_single_record(
            result.get("novice") or {}, mode="novice", **kwargs
        )
        _create_single_record(
            result.get("pro") or {}, mode="pro", **kwargs
        )
        return novice_record
    return _create_single_record(result, mode=mode, **kwargs)


class AccuracyRankingAPIView(APIView):
    """從 speaker_accuracy 快照表取得講者準確率排名"""

    def get(self, request):
        sector = request.query_params.get('sector', '').strip()

        qs = (SpeakerAccuracy.objects.using('summariesdb')
              .filter(sector=sector)
              .exclude(accuracy=None)
              .order_by('-accuracy', '-evaluatable'))

        result = [
            {
                'podcaster': row.podcaster,
                'pass':      row.pass_count,
                'fail':      row.fail_count,
                'total':     row.evaluatable,
                'accuracy':  round(row.accuracy * 100) if row.accuracy is not None else None,
            }
            for row in qs
        ]
        return Response(result)


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
                source_filename=source_filename,
                api_key=api_key,
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
        if not source_filename and not podcaster:
            limit = int(request.query_params.get("limit", 20))
            qs = qs[:limit]
        elif request.query_params.get("limit"):
            qs = qs[:int(request.query_params.get("limit"))]

        result = []
        for s in qs:
            result.append({
                "id": s.id,
                "mode": s.mode,
                "source_filename": s.source_filename,
                "podcaster": s.podcaster,
                "one_sentence_summary": s.one_sentence_summary,
                "tags": s.tags,
                "entities": s.entities,
                "created_at": s.created_at.isoformat(),
                "outlook_calls": s.outlook_calls or [],
            })
        return Response(result)


class SummaryDetailAPIView(APIView):
    def get(self, request, pk):
        try:
            s = SummaryRecord.objects.using("summariesdb").get(pk=pk)
        except SummaryRecord.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)

        outlook_calls = s.outlook_calls or []

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


class BacktestingBySummaryAPIView(APIView):
    """回傳某集摘要的所有 backtesting 紀錄（排除 skip）"""
    def get(self, request, pk):
        records = BacktestingRecord.objects.using("summariesdb").filter(
            summary_id=pk
        ).exclude(result="skip").order_by("id")
        data = [
            {
                "id": r.id,
                "ticker": r.ticker,
                "asset": r.asset,
                "direction": r.direction,
                "thesis": r.thesis,
                "timeframe_raw": r.timeframe_raw,
                "start_time": r.start_time.isoformat() if r.start_time else None,
                "end_time": r.end_time.isoformat() if r.end_time else None,
                "result": r.result,
            }
            for r in records
        ]
        return Response(data)


class AllBacktestingAPIView(APIView):
    """全部 backtesting 紀錄（排除 skip），附帶 podcaster / source_filename。"""
    def get(self, request):
        limit = int(request.query_params.get("limit", 300))
        podcaster = request.query_params.get("podcaster", "").strip()

        qs = BacktestingRecord.objects.using("summariesdb").exclude(
            result="skip"
        ).order_by("-id")

        if podcaster:
            # 透過 summary 的 podcaster 欄位過濾
            from django.db import connections
            with connections["summariesdb"].cursor() as c:
                c.execute("""
                    SELECT b.id, b.summary_id, b.ticker, b.asset, b.direction,
                           b.thesis, b.timeframe_raw,
                           b.start_time, b.end_time, b.result,
                           s.podcaster, s.source_filename
                    FROM backtesting b
                    JOIN summaries_summaryrecord s ON b.summary_id = s.id
                    WHERE b.result != 'skip'
                      AND s.podcaster = %s
                    ORDER BY b.id DESC
                    LIMIT %s
                """, [podcaster, limit])
                rows = c.fetchall()
            cols = ["id","summary_id","ticker","asset","direction","thesis",
                    "timeframe_raw","start_time","end_time","result",
                    "podcaster","source_filename"]
            data = [dict(zip(cols, r)) for r in rows]
            for d in data:
                d["start_time"] = d["start_time"].isoformat() if d["start_time"] else None
                d["end_time"]   = d["end_time"].isoformat()   if d["end_time"]   else None
            return Response(data)

        # 不指定 podcaster：每個節目各取 1 筆最新記錄，優先選還未驗證的（pending）
        from django.db import connections
        with connections["summariesdb"].cursor() as c:
            c.execute("""
                WITH ranked AS (
                    SELECT b.id, b.summary_id, b.ticker, b.asset, b.direction,
                           b.thesis, b.timeframe_raw,
                           b.start_time, b.end_time, b.result,
                           s.podcaster, s.source_filename,
                           ROW_NUMBER() OVER (
                               PARTITION BY s.podcaster
                               ORDER BY
                                   CASE b.result WHEN 'pending' THEN 0 ELSE 1 END,
                                   b.id DESC
                           ) AS rn
                    FROM backtesting b
                    JOIN summaries_summaryrecord s ON b.summary_id = s.id
                    WHERE b.result != 'skip'
                      AND s.podcaster IS NOT NULL AND s.podcaster != ''
                )
                SELECT id, summary_id, ticker, asset, direction,
                       thesis, timeframe_raw,
                       start_time, end_time, result,
                       podcaster, source_filename
                FROM ranked
                WHERE rn = 1
                ORDER BY id DESC
                LIMIT %s
            """, [limit])
            rows = c.fetchall()
        cols = ["id","summary_id","ticker","asset","direction","thesis",
                "timeframe_raw","start_time","end_time","result",
                "podcaster","source_filename"]
        data = [dict(zip(cols, r)) for r in rows]
        for d in data:
            d["start_time"] = d["start_time"].isoformat() if d["start_time"] else None
            d["end_time"]   = d["end_time"].isoformat()   if d["end_time"]   else None
        return Response(data)


class SearchAPIView(APIView):
    """搜尋節目名稱 / 集數標題（含股票代號轉換）"""
    def get(self, request):
        from django.db.models import Q
        q     = request.query_params.get('q', '').strip()
        limit = min(int(request.query_params.get('limit', 12)), 30)

        if not q:
            return Response([])

        # ── TickerMap：股票代號 → 中文名稱（只在標題裡找，不展開到內容）──
        extra_terms = set()
        try:
            ticker_hits = TickerMap.objects.filter(
                Q(ticker__iexact=q) |
                Q(asset_name__icontains=q) |
                Q(zh_name__icontains=q)
            ).values('asset_name', 'zh_name')[:5]
            for row in ticker_hits:
                if row['asset_name']: extra_terms.add(row['asset_name'])
                if row['zh_name']:    extra_terms.add(row['zh_name'])
        except Exception:
            logger.exception("TickerMap search failed")

        all_terms = [q] + list(extra_terms - {q})

        # ── 比對節目名稱、集數標題、Critical Thesis Points ──
        q_filter       = Q()   # 全部條件（用來篩選）
        q_title_filter = Q()   # 僅標題條件（用來排序優先）
        for term in all_terms:
            q_filter |= (
                Q(podcaster__icontains=term)       |
                Q(source_filename__icontains=term) |
                Q(arguments__icontains=term)
            )
            q_title_filter |= (
                Q(podcaster__icontains=term)       |
                Q(source_filename__icontains=term)
            )

        from django.db.models import Case, When, IntegerField, Value
        qs = (
            SummaryRecord.objects.using('summariesdb')
            .filter(q_filter)
            .annotate(
                relevance=Case(
                    When(q_title_filter, then=Value(1)),  # 標題命中 → 排前面
                    default=Value(2),                      # 僅 arguments 命中 → 排後面
                    output_field=IntegerField(),
                )
            )
            .order_by('relevance', '-created_at')[:limit * 4]  # 多抓一些供去重後仍夠 limit 筆
        )

        # 同一集數（source_filename）只保留第一筆（relevance 最高）
        seen_filenames = set()
        result = []
        for s in qs:
            key = s.source_filename or str(s.id)
            if key in seen_filenames:
                continue
            seen_filenames.add(key)
            result.append({
                'id':                   s.id,
                'mode':                 s.mode,
                'podcaster':            s.podcaster,
                'source_filename':      s.source_filename,
                'one_sentence_summary': s.one_sentence_summary,
                'tags':                 s.tags,
                'entities':             s.entities,
                'created_at':           s.created_at.isoformat(),
            })
            if len(result) >= limit:
                break

        return Response(result)


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


class PodcastImagesAPIView(APIView):
    """回傳所有節目的封面圖 {show_name: image_url}"""
    def get(self, request):
        from apps.podcasts.models import Podcast
        podcasts = Podcast.objects.using('podcasts').exclude(image_url='').values('show_name', 'image_url')
        return Response({p['show_name']: p['image_url'] for p in podcasts})