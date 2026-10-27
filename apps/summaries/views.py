import logging

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import SummarizeRequestSerializer, GenerateFromPodcastSerializer
from .services.engine import summarize_from_srt_text
from .services.backtesting import create_backtesting_rows
from .services.sector_accuracy import accuracy_by_sector, ticker_sector_clause
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
        primary_tags=data.get("primary_tags", []),
        analysis_style=data.get("analysis_style", []),
        investor_strategy=data.get("investor_strategy", []),
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
    """
    講者準確率排名。
      - 不帶 sector：讀 speaker_accuracy 快照表的「全體」列（sector=''）。
      - 帶 sector（Rankings 的 8 大類）：不讀快照，直接從 backtesting 依 TICKER_SECTOR 現算
        （見 services/sector_accuracy.py），快照的 sector 欄位目前是另一套細分類，對不上。
    這裡回傳完整資料、不套樣本數門檻——探索頁／節目頁的準確率標籤也在用這支 API；
    排名頁「至少 10 筆才排名」的門檻在 static/js/rankings.js。
    """

    def get(self, request):
        sector = request.query_params.get('sector', '').strip()

        if sector:
            return Response(accuracy_by_sector().get(sector, []))

        qs = (SpeakerAccuracy.objects.using('summariesdb')
              .filter(sector='')
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
        ids = request.query_params.get("ids")

        # 列表只回傳摘要卡片要用的欄位。arguments／glossary_matches／mind_map／investment_takeaways
        # 合計約佔每列 24 KB（整列平均 28 KB），列表完全用不到，defer 之後這些欄位不會從資料庫傳過來。
        # （要看完整內容的是 SummaryDetailAPIView／SummaryMindmapAPIView，各自取單筆。）
        qs = (SummaryRecord.objects.using("summariesdb")
              .defer("arguments", "glossary_matches", "mind_map", "investment_takeaways")
              .order_by("-created_at"))
        if ids:
            # Profile 頁「收藏單集/觀看紀錄」用：拿一批 summary_id 一次查回顯示用資料
            # （標題/節目名稱/日期等），不用逐筆打 /api/summaries/<id>/。
            try:
                id_list = [int(x) for x in ids.split(",") if x.strip()]
            except ValueError:
                return Response([])
            qs = qs.filter(id__in=id_list)
        if source_filename:
            qs = qs.filter(source_filename=source_filename)
        if podcaster:
            qs = qs.filter(podcaster=podcaster)
        if mode:
            qs = qs.filter(mode=mode)
        if not source_filename and not podcaster and not ids:
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
                "primary_tags": s.primary_tags or [],
                "analysis_style": s.analysis_style or [],
                "investor_strategy": s.investor_strategy or [],
                "entities": s.entities,
                "published_at": s.published_at.isoformat() if s.published_at else None,
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
            "episode_id": s.episode_id,
            "source_filename": s.source_filename,
            "podcaster": s.podcaster,
            "mode": s.mode,
            "one_sentence_summary": s.one_sentence_summary,
            "investment_takeaways": s.investment_takeaways,
            "tags": s.tags,
            "entities": s.entities,
            "arguments": s.arguments,
            "outlook_calls": outlook_calls,
            "glossary_matches": s.glossary_matches or [],
            "audio_url": audio_url,
            "published_at": s.published_at.isoformat() if s.published_at else None,
            "created_at": s.created_at.isoformat(),
        })


def _normalize_asset_display_names(data: list) -> list:
    """
    把驗證句要顯示的股票名稱，統一成大表 IndustryClassification 的名稱。

    BacktestingRecord.asset 是每一集 AI 從逐字稿裡直接抓出來的原始字串，同一支股票不同集
    可能寫成「輝達」「NVIDIA」「Nvidia」。前端顯示、Weekly Signals 分組都用這裡回傳的 asset，
    所以統一成同一個名稱後，同一檔標的就會顯示同一個名字、歸在同一張卡。

    查詢順序：
      1. ticker 查大表 display_name（全平台標的名稱以大表為準）
      2. 原始 asset 字串查 TickerMap.zh_name（ticker 空白、或大表沒收錄的標的）
      3. ticker 查 TickerMap.zh_name
      4. 都查不到就維持原始 asset 字串，不會顯示空白
    原始字串另外放在 asset_raw，asset 和 display_name 是統一後的名稱。
    """
    from .services.display_names import display_name_for

    raw_names = {d.get("asset") for d in data if d.get("asset")}
    tickers = {d.get("ticker") for d in data if d.get("ticker")}
    by_asset_name = dict(
        TickerMap.objects.using("summariesdb")
        .filter(asset_name__in=raw_names)
        .exclude(zh_name="")
        .values_list("asset_name", "zh_name")
    ) if raw_names else {}
    by_ticker = dict(
        TickerMap.objects.using("summariesdb")
        .filter(ticker__in=tickers)
        .exclude(zh_name="")
        .values_list("ticker", "zh_name")
    ) if tickers else {}
    for d in data:
        raw = d.get("asset") or ""
        name = (
            display_name_for(d.get("ticker"))
            or by_asset_name.get(raw)
            or by_ticker.get(d.get("ticker"))
            or raw
        )
        d["asset_raw"] = raw
        d["asset"] = name
        d["display_name"] = name
    return data


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
                "evidence_timestamps": r.evidence_timestamps or [],
            }
            for r in records
        ]
        return Response(_normalize_asset_display_names(data))


class AllBacktestingAPIView(APIView):
    """全部 backtesting 紀錄（排除 skip），附帶 podcaster / source_filename。

    只取 pro 摘要的回測列：novice 的回測列是 pro 的複本，全部納入時同一筆預測會出現兩次，
    LIMIT 也會被重複列吃掉一半（節目頁、Weekly Signals 的次數也會加倍）。
    單集頁（BacktestingBySummaryAPIView）照 summary_id 取，novice 頁面仍讀自己那份。
    """
    def get(self, request):
        limit = int(request.query_params.get("limit", 300))
        podcaster = request.query_params.get("podcaster", "").strip()
        before = request.query_params.get("before", "").strip()
        # 只在指定 podcaster 時有意義：Rankings 頁套用產業篩選後點進節目頁，要只看該產業底下的觀點
        sector = request.query_params.get("sector", "").strip()

        qs = BacktestingRecord.objects.using("summariesdb").exclude(
            result="skip"
        ).order_by("-id")

        if podcaster:
            # 透過 summary 的 podcaster 欄位過濾
            from django.db import connections
            sector_filter_sql = ""
            params = [podcaster]
            if sector:
                # Rankings 的 8 大類以 TICKER_SECTOR 為準、不看 ticker_map.sector；不在對照表的標的
                # 歸「其他產業」。判斷邏輯跟排名頁共用 services/sector_accuracy.py，兩邊的「N 筆」才會一致。
                clause = ticker_sector_clause(sector)
                if clause is None:
                    return Response([])
                sector_filter_sql, clause_params = clause
                params.extend(clause_params)
            params.append(limit)
            with connections["summariesdb"].cursor() as c:
                c.execute(f"""
                    SELECT b.id, b.summary_id, b.ticker, b.asset, b.direction,
                           b.thesis, b.timeframe_raw,
                           b.start_time, b.end_time, b.result,
                           s.podcaster, s.source_filename
                    FROM backtesting b
                    JOIN summaries_summaryrecord s ON b.summary_id = s.id
                    WHERE b.result != 'skip'
                      AND s.mode = 'pro'
                      AND s.podcaster = %s
                      {sector_filter_sql}
                    ORDER BY b.id DESC
                    LIMIT %s
                """, params)
                rows = c.fetchall()
            cols = ["id","summary_id","ticker","asset","direction","thesis",
                    "timeframe_raw","start_time","end_time","result",
                    "podcaster","source_filename"]
            data = [dict(zip(cols, r)) for r in rows]
            for d in data:
                d["start_time"] = d["start_time"].isoformat() if d["start_time"] else None
                d["end_time"]   = d["end_time"].isoformat()   if d["end_time"]   else None
            return Response(_normalize_asset_display_names(data))

        # 不指定 podcaster
        from django.db import connections
        with connections["summariesdb"].cursor() as c:
            if before:
                # Weekly Signals：回傳指定日期前的完整紀錄（同一節目可有多筆），供跨節目共識計算使用
                c.execute("""
                    SELECT b.id, b.summary_id, b.ticker, b.asset, b.direction,
                           b.thesis, b.timeframe_raw,
                           b.start_time, b.end_time, b.result,
                           s.podcaster, s.source_filename
                    FROM backtesting b
                    JOIN summaries_summaryrecord s ON b.summary_id = s.id
                    WHERE b.result != 'skip' AND s.mode = 'pro' AND b.start_time < %s
                    ORDER BY b.start_time DESC, b.id DESC
                    LIMIT %s
                """, [before, limit])
            else:
                # 每個節目各取 1 筆最新記錄，優先選還未驗證的（pending）
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
                          AND s.mode = 'pro'
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
        return Response(_normalize_asset_display_names(data))


def _search_visible_hit_terms(s, terms_pairs):
    """
    回傳這筆 SummaryRecord「畫面上真的會顯示出來」的文字裡，命中了 terms_pairs
    （[(原始字串, 小寫字串), ...]）裡的哪些字串。

    只認：節目名稱、集數標題、Critical Thesis Points 的 topic / summary /
    key_data 的 label / value——這些都對應 deep_dive.js 實際 render 出來的欄位。
    故意不比對 key_data.context、related_concepts、evidence_* 這些欄位，因為
    deep_dive.js 從來沒把它們印在畫面上，命中那邊的話使用者點進去會完全找不到
    搜尋詞出現在哪裡（這是先前版本的真實 bug）。
    """
    hit = set()
    title_blob = f"{s.podcaster or ''} {s.source_filename or ''}".lower()
    for t, t_lower in terms_pairs:
        if t_lower in title_blob:
            hit.add(t)

    for arg in (s.arguments or []):
        if not isinstance(arg, dict):
            continue
        haystacks = [arg.get('topic') or '', arg.get('summary') or '']
        for kd in (arg.get('key_data') or []):
            if isinstance(kd, dict):
                haystacks.append(kd.get('label') or '')
                haystacks.append(kd.get('value') or '')
        blob = ' '.join(haystacks).lower()
        for t, t_lower in terms_pairs:
            if t_lower in blob:
                hit.add(t)

    return hit


class SearchAPIView(APIView):
    """搜尋節目名稱 / 集數標題 / 集數裡畫面上看得到的重點內容（含股票代號轉換）"""
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

        all_terms   = [q] + list(extra_terms - {q})
        terms_pairs = [(t, t.lower()) for t in all_terms if t]

        # ── 資料庫先粗篩候選（比對整包 arguments JSON 的文字，含畫面沒顯示的欄位）──
        # 底下再用 Python 那層依 terms_pairs 精篩，只留下真的有命中「畫面上會顯示」
        # 欄位的紀錄，所以這裡要多抓一些候選，篩完才夠湊到 limit 筆。
        q_filter = Q()
        for term in all_terms:
            q_filter |= (
                Q(podcaster__icontains=term)       |
                Q(source_filename__icontains=term) |
                Q(arguments__icontains=term)
            )

        candidates = (
            SummaryRecord.objects.using('summariesdb')
            .filter(q_filter)
            .order_by('-created_at')[:limit * 10]
        )

        # 同一集數（source_filename）只保留第一筆
        seen_filenames = set()
        title_hits, content_hits = [], []
        for s in candidates:
            key = s.source_filename or str(s.id)
            if key in seen_filenames:
                continue

            hit_terms = _search_visible_hit_terms(s, terms_pairs)
            if not hit_terms:
                continue  # 只命中畫面看不到的欄位（如 key_data.context），不算數

            seen_filenames.add(key)
            title_blob = f"{s.podcaster or ''} {s.source_filename or ''}".lower()
            is_title_hit = any(t_lower in title_blob for t, t_lower in terms_pairs if t in hit_terms)

            row = {
                'id':                   s.id,
                'mode':                 s.mode,
                'podcaster':            s.podcaster,
                'source_filename':      s.source_filename,
                'one_sentence_summary': s.one_sentence_summary,
                'tags':                 s.tags,
                'entities':             s.entities,
                'created_at':           s.created_at.isoformat(),
                # 命中的字串，交給前端在 deep dive 頁面裡定位到對應的 Critical Thesis
                # Points 卡片、自動展開＋捲過去，不用使用者自己在整集內容裡找。
                'match_terms':          sorted(hit_terms),
            }
            (title_hits if is_title_hit else content_hits).append(row)
            if len(title_hits) + len(content_hits) >= limit * 2:
                break

        result = (title_hits + content_hits)[:limit]
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