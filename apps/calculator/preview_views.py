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
import math
from datetime import date, timedelta

from django.core.cache import cache
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


IMPLIED_CACHE_SECONDS = 6 * 3600
# 滑桿接受的範圍(簡單年化百分比，用小數表示)，避免離譜的輸入讓倍數失控
FUNDAMENTAL_MIN, FUNDAMENTAL_MAX = -0.90, 5.0
# 「終點本益比」允許的範圍：現在本益比的幾倍(避免離譜輸入讓倍數失控)
PE_TARGET_MIN_RATIO, PE_TARGET_MAX_RATIO = 0.25, 4.0
_RESULT_PATH_KEYS = ("median_path", "band_low", "band_high", "conservative_path", "aggressive_path")
_RESULT_RETURN_KEYS = ("median_return", "p10_return", "p90_return", "conservative_return", "aggressive_return")


def get_implied_assumptions(ticker: str, asof_date, years: int = 5) -> dict:
    """引擎基準隱含的EPS成長(g0)與估值擴張(rho0)，含快取。見 implied_assumptions.py。"""
    as_of = asof_date.date() if hasattr(asof_date, "date") else asof_date
    key = f"implied:{ticker}:{as_of.isoformat() if as_of else 'now-' + date.today().isoformat()}:{years}"
    r = cache.get(key)
    if r is None:
        from apps.calculator.services.implied_assumptions import compute_implied_assumptions
        try:
            r = compute_implied_assumptions(ticker, as_of_date=as_of, years=years)
        except Exception as e:
            r = {"supported": False, "reason": f"計算失敗：{e}"}
        cache.set(key, r, IMPLIED_CACHE_SECONDS)
    return r


def public_implied(r: dict) -> dict:
    """轉成給前端用的格式：成長與估值都用「簡單年化百分比(小數)」，跟使用者看到的一般報酬率同一種算法。"""
    if not r.get("supported"):
        return {"supported": False, "reason": r.get("reason", "不支援")}
    return {
        "supported": True,
        "g0": r["g0_simple"],
        "rho0": math.exp(r["rho0"]) - 1,
        "pe_start": r.get("pe_start"),
        "pe_now": r.get("pe_end"),      # 窗口終點(模擬基準日)當時的本益比
        "pe_pace": r.get("pe_pace"),    # 過去窗口本益比的年化變化(一般百分比)
        "div_log": r.get("div_log"),    # 股息再投入的年化貢獻(對數)，扣掉才是沒還原股息的股價
        "mu_log": r["mu"],
        "eps_start": r["eps_start"],
        "eps_end": r["eps_end"],
        "window_start": r["window_start"].isoformat(),
        "window_end": r["window_end"].isoformat(),
        "years_used": r["years_used"],
    }


def implied_end_pe(pe_now: float, ret: float, years: float, div_log: float, g: float) -> float:
    """
    某個情境終點隱含的本益比＝該情境終點股價 ÷ 假設的終點EPS(再扣掉股息那一塊)：
        現在本益比 ×(1+報酬) ÷ (1+股息貢獻)^T ÷ (1+EPS年成長)^T
    其中 (1+股息貢獻)^T 以 exp(div_log×T) 表示。模擬用的是還原股息的價格，報酬裡混了股息再投入，
    本益比用的是沒還原的股價，所以要把股息那塊除掉。
    """
    return pe_now * (1 + ret) * math.exp(-(div_log or 0.0) * years) / ((1 + g) ** years)


def compute_fundamental_factors(*, years, g0, rho0, eps_growth, valuation, pe_target,
                                pe_now, pe_pace, pe_implied):
    """
    倍數F的來源(純計算，不碰資料庫與模擬)。
      F獲利   ＝ ((1+g1) ÷ (1+g0))^T                       (g1 沒設就等於 g0 → 1)
      F本益比 ＝ 你設的終點本益比 ÷ 基準隱含的終點本益比      (水位式，pe_target 優先)
              或舊式 ((1+r1) ÷ (1+r0))^T                     (年變動式，沒有 pe_target 時)
      F       ＝ F獲利 × F本益比
    pe_target 會被限制在「現在本益比」的 0.25～4 倍。回傳 dict。
    """
    g1 = g0 if eps_growth is None else eps_growth
    f_growth = ((1 + g1) / (1 + g0)) ** years
    pe_default = pe_used = None
    r1 = rho0
    if pe_target is not None and pe_now and pe_implied is not None:
        pe_default = pe_implied
        pe_used = min(max(pe_target, pe_now * PE_TARGET_MIN_RATIO), pe_now * PE_TARGET_MAX_RATIO)
        f_val = pe_used / pe_default
        r1 = (pe_used / pe_now) ** (1 / years) - 1       # 換算成每年變化，給前端顯示用
    else:
        r1 = rho0 if valuation is None else valuation
        f_val = ((1 + r1) / (1 + rho0)) ** years
        if pe_now and pe_pace is not None:
            pe_default = pe_now * (1 + pe_pace) ** years
    return {"g1": g1, "r1": r1, "f_growth": f_growth, "f_val": f_val, "factor": f_growth * f_val,
            "pe_default": pe_default, "pe_used": pe_used}


def apply_fundamental_factor(payload: dict, factor: float) -> None:
    """
    對模擬輸出整體乘上倍數 F(逐日分攤：第t天乘 F^(t/總天數))，不動引擎。
    每條路徑乘同一個倍數，所以分位數也等於乘同一個倍數，數學上跟把調整放進每日漂移等價。
    """
    for key in _RESULT_PATH_KEYS:
        arr = payload.get(key)
        if arr:
            n = len(arr) - 1
            payload[key] = [v * factor ** (i / n) for i, v in enumerate(arr)] if n > 0 else arr
    sp = payload.get("sample_paths")
    if sp:
        n = len(sp[0]) - 1
        payload["sample_paths"] = [[v * factor ** (i / n) for i, v in enumerate(path)] for path in sp] if n > 0 else sp
    for key in _RESULT_RETURN_KEYS:
        if payload.get(key) is not None:
            payload[key] = (1 + payload[key]) * factor - 1


def _parse_fraction(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    return min(max(float(raw), FUNDAMENTAL_MIN), FUNDAMENTAL_MAX)


class BootstrapPathPreviewAPIView(APIView):
    """
    GET參數：
      ticker(手動模式必填) 或 score_id(當集模式必填，優先權較高)
      months(預設12)、risk_score/macro_score(預設0，score_id模式下預設改用那集
      的真實分數，但這裡的query參數如果有給還是會覆蓋)、n_simulations(預設1000，
      上限3000)。
      市場環境(選用)：regime=bull/bear(空頭日占比的絕對水位：偏多約8%、偏空約60%；偏空不低於歷史實際、偏多不高於歷史實際)，
      或 bear_share=0~1 直接指定空頭日占比(優先於regime)。都不給＝不套用，結果跟沒有這個
      功能時一樣。這是使用者自己選的情境假設，不是對「現在是牛市還是熊市」的預測，也沒有
      經過校準(見 market_regime.py)。
      基本面假設(選用)：eps_growth(EPS年化成長，小數，例如0.15)，以及
      pe_target(投資期間結束時的本益比，倍數，例如25)。pe_target 的預設值是
      「現在的本益比 ×(1+過去5年本益比的年化變化)^期間年數」，倍數F＝你設的值÷預設值。
      舊參數 valuation(估值年化變動)仍可用，但 pe_target 優先。
      只傳其中一個時，另一個維持預設，F=1代表沒有調整。
      在API層對模擬輸出乘F，不動引擎，也是使用者的情境假設，未經校準。
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

        regime_preset = request.query_params.get("regime", "").strip() or None
        if regime_preset not in (None, "bull", "bear"):
            regime_preset = None
        regime_bear_share = None
        bear_raw = request.query_params.get("bear_share", "").strip()
        if bear_raw:
            try:
                regime_bear_share = float(bear_raw)
            except ValueError:
                return Response({"error": "bear_share 格式錯誤"}, status=400)
            if not 0.0 <= regime_bear_share <= 1.0:
                return Response({"error": "bear_share 必須介於 0 到 1"}, status=400)

        try:
            eps_growth = _parse_fraction(request.query_params.get("eps_growth", ""))
            valuation = _parse_fraction(request.query_params.get("valuation", ""))
            pe_raw = request.query_params.get("pe_target", "").strip()
            pe_target = float(pe_raw) if pe_raw else None
            if pe_target is not None and not (pe_target > 0 and math.isfinite(pe_target)):
                return Response({"error": "pe_target 必須是正數"}, status=400)
        except ValueError:
            return Response({"error": "eps_growth / valuation / pe_target 格式錯誤"}, status=400)

        light = request.query_params.get("light", "") in ("1", "true")

        actual_line = None
        if light:
            # 只想知道「這組條件下基準隱含的本益比」：不需要真實股價線，報酬是相對的，起點價用100
            start_price = 100.0
        elif asof_date is not None:
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
        if light and seed is None:
            seed = 20261001     # 預設值用固定種子，同樣條件算出同樣的預設值，不會每次小跳動

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
                regime_preset=regime_preset,
                regime_bear_share=regime_bear_share,
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=422)

        payload = {
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
            # 市場環境旋鈕實際套用狀況；沒啟用時是None。
            "regime": result.regime_info,
        }

        # 基準隱含的終點本益比 ＝ 基準終點股價 ÷ 假設的終點EPS：
        #   現在本益比 ×(1+基準報酬) ÷ (1+過去EPS年成長)^期間 ÷ 股息貢獻^期間
        # 模擬用的是還原股息的價格，報酬裡混了股息再投入，本益比用的是沒還原的股價，所以要把股息那塊除掉。
        # 基準報酬已經包含使用者選的市場環境與風險／立場，所以預設值會跟著它們走。
        implied_full = public_implied(get_implied_assumptions(ticker, asof_date))
        pe_baseline = None
        if implied_full.get("supported") and implied_full.get("pe_now"):
            t_years = months / 12
            div_log = implied_full.get("div_log") or 0.0
            pe_baseline = {
                "pe_now": implied_full["pe_now"],
                "pe_pace": implied_full.get("pe_pace"),
                "pe_implied": implied_end_pe(implied_full["pe_now"], payload["median_return"],
                                             t_years, div_log, implied_full["g0"]),
                "g0": implied_full["g0"],
                "base_return": payload["median_return"],
            }
        payload["pe_baseline"] = pe_baseline
        if light:
            return Response({"pe_baseline": pe_baseline, "regime": result.regime_info,
                             "supported": bool(implied_full.get("supported")),
                             "reason": implied_full.get("reason")})

        fundamental = None
        if eps_growth is not None or valuation is not None or pe_target is not None:
            implied = implied_full
            if not implied["supported"]:
                fundamental = {"applied": False, "supported": False, "reason": implied["reason"]}
            else:
                years = months / 12
                g0, r0 = implied["g0"], implied["rho0"]
                pe_now, pe_pace = implied.get("pe_now"), implied.get("pe_pace")
                fx = compute_fundamental_factors(
                    years=years, g0=g0, rho0=r0, eps_growth=eps_growth, valuation=valuation, pe_target=pe_target,
                    pe_now=pe_now, pe_pace=pe_pace,
                    pe_implied=(pe_baseline["pe_implied"] if pe_baseline is not None else None),
                )
                g1, r1, f_growth, f_val, factor = fx["g1"], fx["r1"], fx["f_growth"], fx["f_val"], fx["factor"]
                pe_default, pe_used = fx["pe_default"], fx["pe_used"]
                before = payload["median_return"]
                apply_fundamental_factor(payload, factor)
                fundamental = {
                    "applied": True, "supported": True, "factor": factor,
                    "factor_growth": f_growth, "factor_valuation": f_val,
                    "g0": g0, "rho0": r0, "g1": g1, "rho1": r1,
                    "pe_now": pe_now, "pe_pace": pe_pace, "pe_default": pe_default, "pe_target": pe_used,
                    "median_before": before, "median_after": payload["median_return"],
                }
        payload["fundamental"] = fundamental

        # 保守／基準／積極三個情境在終點時隱含的本益比＝該情境終點股價 ÷ 假設的終點EPS(再扣股息)，
        # 當作合理性檢查：隱含倍數離現在太遠，代表那條股價路徑在基本面上很難成立。
        # 用的是「調整後」的報酬與使用者設的EPS成長，所以滑桿動了這裡也會跟著變。
        pe_scenarios = None
        if pe_baseline is not None:
            t_years = months / 12
            div_log = implied_full.get("div_log") or 0.0
            g_used = fundamental["g1"] if (fundamental and fundamental.get("applied")) else pe_baseline["g0"]

            def _implied_pe(ret):
                return None if ret is None else implied_end_pe(pe_baseline["pe_now"], ret, t_years, div_log, g_used)

            pe_scenarios = {
                "pe_now": pe_baseline["pe_now"],
                "conservative": _implied_pe(payload.get("conservative_return")),
                "baseline": _implied_pe(payload.get("median_return")),
                "aggressive": _implied_pe(payload.get("aggressive_return")),
            }
        payload["pe_scenarios"] = pe_scenarios
        return Response(payload)


class ImpliedAssumptionsAPIView(APIView):
    """
    給前端「基本面假設」滑桿的預設值：這檔股票在抽樣窗口(5年)裡，引擎基準隱含的
    EPS年化成長 g0 與估值擴張 rho0(含股息再投入)，以及起訖近四季EPS。

    GET參數：ticker(手動模式)或 score_id(當集模式，窗口錨在那一集發布日)。
    沒有足夠財報(非台股前幾十檔、指數、ETF、EPS為負等)時 supported=false 並附 reason，
    前端據此隱藏滑桿。
    """

    def get(self, request):
        score_id = request.query_params.get("score_id", "").strip()
        ticker = request.query_params.get("ticker", "").strip()
        asof_date = None
        if score_id:
            from apps.summaries.models import StockSentimentScore
            try:
                score = (StockSentimentScore.objects.using("summariesdb")
                         .select_related("summary").get(id=score_id))
            except StockSentimentScore.DoesNotExist:
                return Response({"error": "找不到這筆分數紀錄(score_id)"}, status=404)
            ticker = score.ticker
            asof_date = score.summary.published_at
        if not ticker:
            return Response({"error": "請提供 ticker 或 score_id"}, status=400)
        return Response({"ticker": ticker, **public_implied(get_implied_assumptions(ticker, asof_date))})


class EpisodeListPreviewAPIView(APIView):
    """
    給預覽頁的「選集數」下拉選單用。

    2026-10-14改版：只列「有驗證句、驗證句有起訖日、且還沒到期(end_time >= 今天)」的集數
    (每一筆都是 podcaster 對某檔股票講出了「方向＋可驗證的時間」的說法，而且那段時間還沒結束)，
    所以每一筆都一定有 default_months(回測月數)。
    原本是列出所有有分數的 StockSentimentScore，只要這集提到那檔股票就會出現，其中約三分之二
    沒有可驗證的時間，畫面上「回測約N個月」就時有時無。

    資料來源仍然要用 StockSentimentScore：集模式需要它的 risk_score/macro_score 與 base/
    annual_vol(價格參數)，這些不在驗證句表(BacktestingRecord)裡；兩張表用 (summary_id, ticker)
    對上同一集同一支股票。依發布日新到舊排序，最多回傳200筆避免選單過長。

    2026-10-01修正：同一集(episode_id)的pro/novice兩種mode各自有一筆SummaryRecord，
    backtesting/分數計算對兩者都各跑一次，導致同一支股票在同一集出現兩筆內容幾乎
    一樣的紀錄，下拉選單看起來像重複。依(episode_id, ticker)去重，同時存在時優先留pro版
    (內容較完整、不是簡化過的版本)，novice版只在沒有pro版時才保留。
    """

    def get(self, request):
        from apps.summaries.models import BacktestingRecord, StockSentimentScore

        # 1) 驗證句：有 ticker、有起訖日的才算(「未來幾年」這種換不成日期的被略過，不列入)。
        #    用(summary_id, ticker)當key，這是BacktestingRecord跟StockSentimentScore唯一共同、
        #    能對上同一支股票同一集的欄位。同一集同一檔有多句時，以最後一筆(id最大)為準。
        # 只撈「最近發布」的驗證句：選單最多200集，不需要整張表(約8千筆)。
        # pro/novice 各一份、同一集同一檔可能多句，所以多抓一些(1500筆)再去重、截斷。
        # 只列「還沒到期」的驗證句(end_time >= 今天)：已經到期的那些，真實結果已經出來了，
        # 屬於回測驗證的範圍，不是「接下來要看的標的」，所以不放進選單。
        # 快取 key 帶日期，避免跨日之後還沿用昨天的清單。
        today = date.today()
        cache_key = f"episode-list-preview:v3:{today.isoformat()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        bt_rows = (
            BacktestingRecord.objects.using("summariesdb")
            .filter(start_time__isnull=False, end_time__isnull=False, end_time__gte=today)
            .exclude(ticker="")
            .order_by("-summary__published_at", "id")[:1500]
            .values("summary_id", "ticker", "start_time", "end_time")
        )
        months_by_key = {}
        for bt in bt_rows:
            days = (bt["end_time"] - bt["start_time"]).days
            if days > 0:
                # 約略換算成月數，不用精確到日；至少算1個月，避免0個月的模擬。
                months_by_key[(bt["summary_id"], bt["ticker"])] = max(1, round(days / 30))

        # 2) 對應的分數紀錄(集模式要用)。
        #    注意：不要用 select_related("summary")——SummaryRecord 帶著整份摘要文字，
        #    撈上千筆會慢到十幾秒；只取需要的欄位，摘要欄位另外一次撈(實測 0.3 秒 vs 10 秒)。
        from apps.summaries.models import SummaryRecord

        summary_ids = {k[0] for k in months_by_key}
        score_rows = list(
            StockSentimentScore.objects.using("summariesdb")
            .filter(summary_id__in=summary_ids, base__isnull=False, annual_vol__isnull=False)
            .values("id", "summary_id", "episode_id", "ticker", "asset_name", "risk_score", "macro_score", "rationale")
        )
        summaries = {
            r["id"]: r
            for r in SummaryRecord.objects.using("summariesdb")
            .filter(id__in=summary_ids)
            .values("id", "episode_id", "mode", "published_at", "podcaster")
        }

        dedup_by_key = {}
        for r in score_rows:
            if (r["summary_id"], r["ticker"]) not in months_by_key:
                continue   # 同一集裡的驗證句是講別檔股票，這檔沒有
            sm = summaries.get(r["summary_id"])
            if not sm or not sm["published_at"]:
                continue
            r = {**r, "_sm": sm}
            key = (sm["episode_id"], r["ticker"])
            existing = dedup_by_key.get(key)
            if existing is None or (existing["_sm"]["mode"] != "pro" and sm["mode"] == "pro"):
                dedup_by_key[key] = r
        rows = sorted(dedup_by_key.values(), key=lambda r: r["_sm"]["published_at"], reverse=True)[:200]

        payload = {
            "episodes": [
                {
                    "score_id": r["id"],
                    "summary_id": r["summary_id"],  # 給前端「跳轉到原文」連結用，見bootstrap_path.html
                    "episode_id": r["episode_id"],  # 給「從deep dive頁面試算按鈕跳轉過來」配對用
                    "ticker": r["ticker"],
                    "asset_name": r["asset_name"],
                    "published_at": r["_sm"]["published_at"].date().isoformat(),
                    "podcaster": r["_sm"]["podcaster"],
                    "risk_score": r["risk_score"],
                    "macro_score": r["macro_score"],
                    # AI對這集這支股票判斷risk/macro分數時，順便產生的一句話理由(≤200字)，
                    # 跟這筆score同一張表存的欄位，撈這批的成本很低，不用另外呼叫。
                    "rationale": r["rationale"],
                    # 來自這集對這支股票的驗證句時間範圍(約略月數)。改版後選單只列有驗證句的，
                    # 所以這裡一定有值；前端對None仍保留原本手動設定的月數(深連結補進來的集可能沒有)。
                    "default_months": months_by_key.get((r["summary_id"], r["ticker"])),
                }
                for r in rows
            ]
        }
        cache.set(cache_key, payload, 300)   # 清單幾乎不變，快取5分鐘
        return Response(payload)


class StockSearchPreviewAPIView(APIView):
    """
    手動輸入股票用的搜尋建議：可以輸入代號或中文名稱(例如「台積電」「2330」「輝達」)。

    GET參數：q。回傳 {"results": [{ticker, name, market, exact}]}，最多8筆。
    資料來源(依序合併、去重)：
      1. TickerMap(summariesdb，約250檔，台股與美股的中文名稱，團隊維護)
      2. 證交所上市股票清單(views._get_tw_stock_map，24小時快取)，補 TickerMap 沒收的台股
      3. 輸入看起來就是代號(例如 AMD)但上面都找不到時，原樣當作代號回傳，不擋掉使用者
    排序：代號／名稱完全相同 > 代號開頭 > 名稱開頭 > 名稱或代號包含。
    exact=True 代表「代號或名稱跟輸入完全一樣」，前端用它判斷是不是可以直接帶入。
    """

    MAX_RESULTS = 8

    @staticmethod
    def _market_label(ticker: str, exchange: str = "") -> str:
        t = ticker.upper()
        if t.endswith(".TWO"):
            return "上櫃"
        if t.endswith(".TW"):
            return "台股"
        if t.startswith("^"):
            return "指數"
        if "=" in t:
            return "商品"
        if t.endswith("-USD"):
            return "加密貨幣"
        if "." in t:
            return exchange or "海外"
        return "美股"

    def get(self, request):
        import re

        q = request.query_params.get("q", "").strip()
        if not q or len(q) > 40:
            return Response({"results": []})
        ql = q.lower()

        # TickerMap 只有約250筆，整張撈起來快取1小時，在記憶體裡比對
        rows = cache.get("ticker-map-rows:v1")
        if rows is None:
            try:
                from apps.summaries.models import TickerMap
                rows = list(TickerMap.objects.using("summariesdb").values("ticker", "asset_name", "zh_name", "exchange"))
            except Exception:
                rows = []
            cache.set("ticker-map-rows:v1", rows, 3600)

        found = {}   # ticker -> {ticker, name, market, rank, exact}

        def consider(ticker: str, names: list, exchange: str = ""):
            names = [n for n in names if n]
            if not names:
                return
            code = re.sub(r"\.[A-Za-z]+$", "", ticker).lower()
            t_low = ticker.lower()
            best, exact = None, False
            for n in names:
                nl = n.lower()
                if nl == ql:
                    r, ex = 0, True
                elif nl.startswith(ql):
                    r, ex = 2, False
                elif ql in nl:
                    r, ex = 3, False
                else:
                    continue
                if best is None or r < best:
                    best, exact = r, ex
            if code == ql or t_low == ql:
                best, exact = 0, True
            elif t_low.startswith(ql) or code.startswith(ql):
                best = 1 if best is None else min(best, 1)
            elif ql in t_low and best is None:
                best = 4
            if best is None:
                return
            display = next((n for n in names if re.search(r"[一-鿿]", n)), names[0])
            cur = found.get(ticker)
            if cur is None or best < cur["rank"]:
                found[ticker] = {
                    "ticker": ticker, "name": display, "market": self._market_label(ticker, exchange),
                    "rank": best, "exact": exact,
                }

        for r in rows:
            consider(r["ticker"], [r.get("zh_name"), r.get("asset_name")], r.get("exchange") or "")

        # 證交所上市清單(含 TickerMap 沒收的)。查不到時(連線失敗)就只用上面的結果。
        try:
            from .views import _get_tw_stock_map
            for code, name in (_get_tw_stock_map() or {}).items():
                consider(f"{code}.TW", [name], "TWSE")
        except Exception:
            pass

        # 輸入本身就像代號(例如 AMD、BRK-B、2330.TW)，但都沒找到：原樣當作代號給使用者選
        if re.fullmatch(r"[A-Za-z0-9.\-^=]{1,15}", q) and not any(v["ticker"].lower() == ql for v in found.values()):
            tk = q.upper()
            if tk.isdigit():
                tk = tk + ".TW"
            found.setdefault(tk, {"ticker": tk, "name": tk, "market": self._market_label(tk), "rank": 9, "exact": False})

        results = sorted(found.values(), key=lambda v: (v["rank"], len(v["name"]), v["ticker"]))[: self.MAX_RESULTS]
        return Response({"results": [{k: v[k] for k in ("ticker", "name", "market", "exact")} for v in results]})


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
