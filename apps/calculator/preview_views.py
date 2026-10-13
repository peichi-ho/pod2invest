# apps/calculator/preview_views.py
"""
B方案(歷史拔靴逐步模擬)的API——雖然檔名/類別名還留著「preview」，但這支其實是
正式試算計算機頁面(templates/pages/calculator.html的「開始模擬」按鈕，見
static/js/calculator.js的B_API_URL)實際在打的endpoint，跟ScenarioAPIView/
scenario.py(給「從節目挑選」時預填滑桿用)是兩條獨立路徑，不是互相替代關係。

2026-10-01起改用 bootstrap_path_simulator_v2.py（log空間逐日套用shift/widen、
保證單日不超過台股漲跌停10%、CRPS重新校準過，且改用P10~P90取代原本的P30~P70，
詳見該檔案docstring），不是原本的 bootstrap_path_simulator.py（v1保留但不再被
呼叫）。

兩種模式：
  手動模式：只給 ticker，起始股價抓「現在」即時股價，risk/macro預設0，每次呼叫
            都是全新隨機種子(不保證重跑結果一樣)，單純用來試玩不同股票的行為。
  當集模式：給 score_id，起始股價/歷史抽樣母體都錨在「那一集的發布日」(asof_date)，
            risk/macro預設帶入那一集真實的分數，種子固定用score_id算(保證同一集
            每次重跑結果一模一樣)，並且會多算一段「發布日到現在」的真實股價路徑
            (actual_line)，給前端疊圖用。
"""
from datetime import timedelta

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.calculator.services.bootstrap_path_simulator_v2 import run_bootstrap_path_simulation_v2

MAX_SIMULATIONS = 3000  # 瀏覽器互動用的預覽工具，限制上限避免單次請求跑太久
N_SAMPLE_PATHS_RETURNED = 20  # 前端畫背景線用，不用把模組預設的50條全部傳過去


def _fetch_actual_price_path(ticker: str, asof_date, months: int) -> list:
    """
    抓這支股票從asof_date開始，實際往後 months 個月的真實股價，每個月一筆，
    跟 apps/calculator/views.py 的同名函式邏輯一致(那邊是給正式系統用，這裡獨立
    重新實作一份，不直接import，維持B方案模組群刻意跟正式系統不共用程式碼的慣例)。
    還沒到的月份回傳 None，不會捏造還沒發生的資料。
    """
    import yfinance as yf
    from datetime import datetime, timezone

    def add_months(d, n):
        month = d.month - 1 + n
        year = d.year + month // 12
        month = month % 12 + 1
        day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return d.replace(year=year, month=month, day=day)

    start = asof_date.date() if hasattr(asof_date, "date") else asof_date
    today = datetime.now(timezone.utc).date()
    target_dates = [add_months(start, m) for m in range(months + 1)]

    fetch_end = min(target_dates[-1], today) + timedelta(days=8)
    hist = yf.Ticker(ticker).history(
        start=(start - timedelta(days=7)).isoformat(), end=fetch_end.isoformat(), auto_adjust=True,
    )
    if hist.empty:
        raise ValueError("查無股價資料")

    closes = {ts.date(): float(row["Close"]) for ts, row in hist.iterrows()}

    def nearest_close(target, max_lookback=7):
        for back in range(max_lookback + 1):
            d = target - timedelta(days=back)
            if d in closes:
                return closes[d]
        return None

    return [None if d > today else nearest_close(d) for d in target_dates]


class BootstrapPathPreviewAPIView(APIView):
    """
    GET參數：
      ticker(手動模式必填) 或 score_id(當集模式必填，優先權較高)
      months(預設12)、risk_score/macro_score(預設0，score_id模式下預設改用那集
      的真實分數，但這裡的query參數如果有給還是會覆蓋)、n_simulations(預設1000，
      上限3000)。
    """

    def get(self, request):
        score_id = request.query_params.get("score_id", "").strip()
        ticker = request.query_params.get("ticker", "").strip()

        score = None
        asof_date = None
        if score_id:
            from apps.summaries.models import StockSentimentScore
            try:
                score = (
                    StockSentimentScore.objects.using("summariesdb")
                    .select_related("summary")
                    .get(id=score_id)
                )
            except StockSentimentScore.DoesNotExist:
                return Response({"error": "找不到這筆分數紀錄(score_id)"}, status=404)
            ticker = score.ticker
            asof_date = score.summary.published_at

        if not ticker:
            return Response({"error": "請提供 ticker 或 score_id"}, status=400)

        try:
            months = int(request.query_params.get("months", 12))
            n_simulations = int(request.query_params.get("n_simulations", 1000))
            risk_raw = request.query_params.get("risk_score", "").strip()
            macro_raw = request.query_params.get("macro_score", "").strip()
            default_risk = score.risk_score if score is not None else 0.0
            default_macro = score.macro_score if score is not None else 0.0
            risk_score = float(risk_raw) if risk_raw else default_risk
            macro_score = float(macro_raw) if macro_raw else default_macro
        except ValueError:
            return Response({"error": "參數格式錯誤"}, status=400)

        if months <= 0:
            return Response({"error": "months 必須是正數"}, status=400)
        n_simulations = min(max(n_simulations, 100), MAX_SIMULATIONS)

        actual_line = None
        if asof_date is not None:
            try:
                actual_line = _fetch_actual_price_path(ticker, asof_date, months)
                start_price = actual_line[0]
            except Exception as e:
                return Response({"error": f"抓不到 {ticker} 當時的股價: {e}"}, status=502)
            if start_price is None:
                return Response({"error": f"抓不到 {ticker} 當時({asof_date.date()})的股價"}, status=502)
        else:
            import yfinance as yf
            try:
                closes = yf.Ticker(ticker).history(period="5d", auto_adjust=True)["Close"].dropna()
                if closes.empty:
                    return Response({"error": f"抓不到 {ticker} 的股價，確認代號是否正確"}, status=502)
                start_price = float(closes.iloc[-1])
            except Exception as e:
                return Response({"error": f"抓取股價失敗: {e}"}, status=502)

        # 當集模式：種子固定用score_id，保證同一集每次重跑結果一模一樣；
        # 手動模式：種子留None，每次都是全新隨機結果，方便試玩不同輸入。
        seed = int(score_id) if score_id else None

        try:
            result = run_bootstrap_path_simulation_v2(
                ticker=ticker,
                start_price=start_price,
                horizon_months=months,
                risk_score=risk_score,
                macro_score=macro_score,
                n_simulations=n_simulations,
                as_of_date=asof_date.date() if asof_date is not None else None,
                seed=seed,
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=422)

        return Response({
            "ticker": ticker,
            "start_price": start_price,
            "months": months,
            "risk_score": risk_score,
            "macro_score": macro_score,
            "n_simulations": result.n_simulations,
            "history_days_used": result.history_days_used,
            "time_steps": result.time_steps,
            "median_path": result.median_path,
            "band_low": result.band_low,
            "band_high": result.band_high,
            "conservative_path": result.conservative_path,
            "aggressive_path": result.aggressive_path,
            "sample_paths": result.sample_paths[:N_SAMPLE_PATHS_RETURNED],
            "median_return": result.median_return,
            "p10_return": result.p10_return,
            "p90_return": result.p90_return,
            "conservative_return": result.conservative_return,
            "aggressive_return": result.aggressive_return,
            "score_id": score_id or None,
            "asof_date": asof_date.date().isoformat() if asof_date is not None else None,
            "asset_name": score.asset_name if score is not None else None,
            "actual_line": actual_line,
        })


class EpisodeListPreviewAPIView(APIView):
    """
    給預覽頁的「選集數」下拉選單用：列出有 base/annual_vol 資料(=可以拿來當集模式
    使用)的 StockSentimentScore，依發布日新到舊排序，最多回傳200筆避免選單過長。

    2026-10-01修正：同一集(episode_id)的pro/novice兩種mode各自有一筆SummaryRecord，
    backtesting/分數計算對兩者都各跑一次，導致同一支股票在同一集出現兩筆內容幾乎
    一樣的紀錄，下拉選單看起來像重複。改成抓比200多一些的候選池，依(episode_id,
    ticker)去重，同時存在時優先留pro版(內容較完整、不是簡化過的版本)，novice版
    只在該(episode_id,ticker)沒有pro版時才保留，去重後再截斷回200筆。
    """

    def get(self, request):
        from apps.summaries.models import StockSentimentScore

        from apps.summaries.models import BacktestingRecord

        # 抓比200多的候選池(pro/novice重複大約佔一半，400大致夠去重後還有200筆)，
        # 去重後再截斷，避免去重後筆數不足200筆。
        candidate_rows = (
            StockSentimentScore.objects.using("summariesdb")
            .filter(base__isnull=False, annual_vol__isnull=False)
            .select_related("summary")
            .order_by("-summary__published_at")[:400]
        )

        dedup_by_key = {}
        for r in candidate_rows:
            key = (r.summary.episode_id, r.ticker)
            existing = dedup_by_key.get(key)
            if existing is None or (existing.summary.mode != "pro" and r.summary.mode == "pro"):
                dedup_by_key[key] = r
        rows = sorted(
            dedup_by_key.values(), key=lambda r: r.summary.published_at, reverse=True
        )[:200]

        # 一次把這批episode對應到的backtesting紀錄(start_time/end_time)全部撈出來，
        # 不要每筆episode各自查一次DB(避免N+1)。用(summary_id, ticker)當key配對，
        # 這是BacktestingRecord跟StockSentimentScore唯一共同、能對上同一支股票同一集的欄位。
        summary_ids = [r.summary_id for r in rows]
        bt_rows = (
            BacktestingRecord.objects.using("summariesdb")
            .filter(summary_id__in=summary_ids, start_time__isnull=False, end_time__isnull=False)
            .values("summary_id", "ticker", "start_time", "end_time")
        )
        months_by_key = {}
        for bt in bt_rows:
            key = (bt["summary_id"], bt["ticker"])
            days = (bt["end_time"] - bt["start_time"]).days
            if days > 0:
                # 約略換算成月數，不用精確到日；至少算1個月，避免0個月的模擬。
                months_by_key[key] = max(1, round(days / 30))

        return Response({
            "episodes": [
                {
                    "score_id": r.id,
                    "summary_id": r.summary_id,  # 給前端「跳轉到原文」連結用，見bootstrap_path.html
                    "episode_id": r.episode_id,  # 給「從deep dive頁面試算按鈕跳轉過來」配對用
                    "ticker": r.ticker,
                    "asset_name": r.asset_name,
                    "published_at": r.summary.published_at.date().isoformat() if r.summary.published_at else None,
                    "podcaster": r.summary.podcaster,
                    "risk_score": r.risk_score,
                    "macro_score": r.macro_score,
                    # AI對這集這支股票判斷risk/macro分數時，順便產生的一句話理由(≤200字)，
                    # 跟這筆score同一張表存的欄位，撈這批的成本很低，不用另外呼叫。
                    "rationale": r.rationale,
                    # 來自這集對這支股票的backtesting thesis時間範圍(約略月數)；
                    # 沒有對應紀錄時是None，前端會保留原本手動設定的月數，不會硬套。
                    "default_months": months_by_key.get((r.summary_id, r.ticker)),
                }
                for r in rows
            ]
        })


class EpisodeSourceTextPreviewAPIView(APIView):
    """
    給預覽頁「顯示這集原始段落」用：回傳分類AI判斷risk/macro分數時，實際讀到的
    完整輸入內容(不是AI事後補的一句話理由，是真正的判斷依據)。

    這裡刻意用 build_classification_context() 組出完整context，不是只給「個股：XXX」
    那一段——分類prompt實際餵給AI的內容除了個股段落，還包含「總體經濟環境(共用背景)」
    跟「操作策略與建議/風險提示(有點名這支股票)」，之前只顯示個股段落，會讓人誤以為
    AI判斷的risk_score沒有依據，但其實依據可能在後面這兩段裡，只是沒顯示出來——
    這裡改成跟分類prompt看到的內容一致，才能正確判斷AI的分類是不是真的有問題。

    刻意獨立成一個endpoint、只在使用者選定某一集時才呼叫(不是跟episodes list
    一起回傳)，因為要解析 arguments JSON、可能還要呼叫 resolve_ticker，對200筆
    一次全部算太浪費，只在真的要看的那一筆才算。
    """

    def get(self, request):
        score_id = request.query_params.get("score_id", "").strip()
        if not score_id:
            return Response({"error": "請提供 score_id"}, status=400)

        from apps.summaries.models import StockSentimentScore
        from apps.summaries.services.sentiment_score import (
            find_stock_topics_with_fallback, build_classification_context,
        )

        try:
            score = (
                StockSentimentScore.objects.using("summariesdb")
                .select_related("summary")
                .get(id=score_id)
            )
        except StockSentimentScore.DoesNotExist:
            return Response({"error": "找不到這筆分數紀錄(score_id)"}, status=404)

        topics = find_stock_topics_with_fallback(score.summary)
        matched = next((t for t in topics if t["asset_name"] == score.asset_name), None)
        topic_summary = matched["topic"].get("summary", "") if matched else ""

        full_context = ""
        if matched:
            full_context, _stance_hint = build_classification_context(
                score.summary, score.asset_name, score.ticker, matched["topic"],
            )

        return Response({
            "score_id": score.id,
            "rationale": score.rationale,
            "topic_summary": topic_summary,
            # AI實際看到的完整輸入(個股段落+總經背景+風險提示)，判斷分類合不合理要看這個，
            # 不是只看topic_summary。matched為None時(fallback內容組不出完整context)是空字串。
            "full_context": full_context,
        })
