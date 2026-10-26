# apps/calculator/thesis_views.py
"""
論點情境試算的 API(第三步)：計算機「從節目挑選」模式下，選定一集一檔股票後，
列出這集對這檔股票的論點與拆出的情境，讓使用者勾選、調整參數，算出 12 個月後的
EPS 與目標價，並標出這個報酬落在歷史股價模擬(B方案拔靴)的第幾百分位。

情境來自 ThesisScenarioExtraction(批次算好存在資料庫，見 apps/summaries/services/thesis_scenario.py)；
基準數字與推算在 apps/calculator/services/thesis_valuation.py。一律以「節目發布日」為基準日：
用當天股價與當時已公告的財報——拿今天的股價去比舊節目的論點會得出誤導的結果
(實測：台積電那集用今天股價算是 −38%，用發布日算是 +6%)。

GET 參數：
  score_id(必填)：計算機選集清單的那一筆 StockSentimentScore
  thesis_id：同一集同一檔有好幾句論點時選哪一句(ThesisScenarioExtraction.id)，不給用第一句
  checked：勾選的情境序號，逗號分隔(例如 0,2,3)；不給 = 都不勾(Base)
  revenue_growth / gross_margin / opex_ratio / operating_margin / net_margin / pe / ps / eps：
      使用者手動覆寫的參數(小數，例如 0.25 = 25%)，蓋過情境算出來的值
"""
import numpy as np
from django.core.cache import cache
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.calculator.services.thesis_valuation import (
    apply_scenarios, compute_baseline, project, scenario_levers, waterfall,
)

PROMPT_VERSION = "v5"
BASELINE_CACHE_SECONDS = 6 * 3600     # 基準數字要打 yfinance，同一檔同一天的結果快取起來
HORIZON_MONTHS = 12                    # 目標價是「12 個月後」，跟拔靴模擬比較時也用 12 個月

TYPE_LABELS = {
    "DEMAND_UP": "需求、訂單增加", "NEW_BUSINESS": "新產品、新客戶", "PRICE_UP": "產品漲價",
    "MIX_UPGRADE": "產品組合升級", "COST_EFFICIENCY": "成本下降", "CAPACITY_EXPANSION": "擴產",
    "M_AND_A": "併購", "DEMAND_DOWN": "需求放緩", "COMPETITION": "競爭加劇", "COST_UP": "成本上升",
    "PRICE_DOWN": "產品跌價", "RERATING_UP": "估值偏低", "RERATING_DOWN": "估值偏高",
    "MACRO_EASING": "降息、資金寬鬆", "MACRO_TIGHTENING": "升息、資金緊縮", "FINANCIAL_RISK": "財務風險",
    "REGULATORY_RISK": "法規風險", "EPS_FORECAST": "EPS 預估", "ANALYST_TARGET": "目標價位", "TECHNICAL_FLOW": "技術面、籌碼面",
}
LEVER_LABELS = {
    "revenue_growth": "營收成長", "gross_margin": "毛利率", "opex_ratio": "費用率",
    "pe": "本益比", "eps_multiplier": "EPS",
}
OVERRIDABLE = ("revenue_growth", "gross_margin", "opex_ratio", "operating_margin", "net_margin", "pe", "ps")


def _scenario_view(s: dict, idx: int) -> dict:
    """給前端顯示用：情境的中文名稱、影響哪些參數、能不能勾選。"""
    t = s.get("type")
    figures = {k: v for k, v in (s.get("figures") or {}).items() if v is not None}
    effects = [f"{LEVER_LABELS[k]}{'↑' if v > 0 else '↓'}" for k, v in scenario_levers(s).items()]
    if t == "EPS_FORECAST":
        effects = ["直接指定 EPS"] if figures.get("eps") else ["EPS 上修（沒有數字，不影響試算）"]
    # 技術面、目標價只顯示不試算；沒有數字的 EPS 預估也沒東西可以算
    checkable = t not in ("TECHNICAL_FLOW", "ANALYST_TARGET") and not (t == "EPS_FORECAST" and not figures.get("eps"))
    return {
        "idx": idx, "type": t, "label": TYPE_LABELS.get(t, t), "stance": s.get("stance"),
        "chain": s.get("chain") or [], "quote": s.get("quote", ""), "figures": figures,
        "effects": effects, "checkable": checkable,
        "provisional": t in ("FINANCIAL_RISK", "REGULATORY_RISK", "MACRO_EASING", "MACRO_TIGHTENING"),
        "source": s.get("source"),                   # 目標價位：analyst(法人) / podcast(節目觀點)
        "figures_source": s.get("figures_source"),   # thesis = 數字取自論點摘要，不是原文直接講的
    }


def _cached_baseline(ticker, as_of):
    key = f"thesis-baseline:{ticker}:{as_of.isoformat()}"
    b = cache.get(key)
    if b is None:
        b = compute_baseline(ticker, as_of=as_of)
        cache.set(key, b, BASELINE_CACHE_SECONDS)
    return b


def _bootstrap_percentile(ticker, as_of, start_price, implied_return):
    """
    目標價報酬落在「12 個月歷史拔靴模擬」終點報酬分布的第幾百分位(0~100)。
    模擬的終點報酬快取起來，使用者拉滑桿時只重算百分位，不重跑模擬。
    """
    if implied_return is None or not start_price:
        return None
    key = f"thesis-boot:{ticker}:{as_of.isoformat()}"
    returns = cache.get(key)
    if returns is None:
        from apps.calculator.services.bootstrap_path_simulator_v2 import run_bootstrap_path_simulation_v2
        try:
            result = run_bootstrap_path_simulation_v2(
                ticker=ticker, start_price=start_price, horizon_months=HORIZON_MONTHS,
                n_simulations=1000, as_of_date=as_of, seed=0,
            )
            returns = sorted(result.terminal_returns)
        except Exception:
            returns = []
        cache.set(key, returns, BASELINE_CACHE_SECONDS)
    if not returns:
        return None
    return float(np.searchsorted(returns, implied_return) / len(returns) * 100)


def _waterfall_label(s: dict) -> str:
    """瀑布圖上的情境名稱：帶數字的情境把數字一起寫上，才分得出是哪一個 EPS/本益比。"""
    f = s["figures"]
    if f.get("eps"):
        return f"{s['label']} {f['eps']:g}" + (f"（{f['year']}）" if f.get("year") else "")
    if f.get("pe"):
        return f"{s['label']} {f['pe']:g} 倍"
    return s["label"]


def _parse_float(raw):
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def _baseline_view(b) -> dict:
    keys = ("route", "reason", "price", "ttm_end", "revenue", "gross_margin", "opex_ratio",
            "operating_margin", "net_margin", "eps", "pe", "ps")
    out = {k: getattr(b, k) for k in keys}
    out["ttm_end"] = b.ttm_end.isoformat() if b.ttm_end else None
    out["history"] = b.history
    return out


class ThesisValuationAPIView(APIView):
    def get(self, request):
        from apps.summaries.models import StockSentimentScore, ThesisScenarioExtraction

        score_id = request.query_params.get("score_id", "").strip()
        if not score_id:
            return Response({"error": "請提供 score_id"}, status=400)
        try:
            score = StockSentimentScore.objects.using("summariesdb").select_related("summary").get(id=score_id)
        except (StockSentimentScore.DoesNotExist, ValueError):
            return Response({"error": "找不到這筆分數紀錄"}, status=404)
        if not score.summary.published_at:
            return Response({"error": "這一集沒有發布日，無法決定試算基準日"}, status=422)
        as_of = score.summary.published_at.date()

        # 同一集同一檔的所有論點(同一句論點可能有好幾筆重複的驗證句，依論點文字去重)
        rows = list(
            ThesisScenarioExtraction.objects.using("summariesdb")
            .filter(summary_id=score.summary_id, ticker=score.ticker, prompt_version=PROMPT_VERSION)
            .exclude(status=ThesisScenarioExtraction.STATUS_FAILED)
            .select_related("backtest_record").order_by("id")
        )
        seen, theses = set(), []
        for r in rows:
            text = r.backtest_record.thesis.strip()
            if text not in seen:
                seen.add(text)
                theses.append(r)

        payload = {
            "ticker": score.ticker, "asset_name": score.asset_name, "as_of": as_of.isoformat(),
            "horizon_months": HORIZON_MONTHS,
            "theses": [{"id": r.id, "thesis": r.backtest_record.thesis, "direction": r.backtest_record.direction,
                        "timeframe": r.backtest_record.timeframe_raw, "status": r.status} for r in theses],
        }
        if not theses:
            payload.update(available=False, reason="這一集對這檔股票的論點還沒有拆解成情境")
            return Response(payload)

        thesis_id = _parse_float(request.query_params.get("thesis_id"))
        current = next((r for r in theses if r.id == thesis_id), theses[0])
        scenarios = [_scenario_view(s, i) for i, s in enumerate(current.scenarios)]
        payload.update(thesis_id=current.id, scenarios=scenarios,
                       no_driver_reason=current.no_driver_reason if current.status == "no_driver" else "")

        # 目標價位只當參考，不參與試算；source 分法人與節目觀點
        payload["analyst_targets"] = [
            {"low": s["figures"].get("target_low"), "high": s["figures"].get("target_high"), "quote": s["quote"],
             "source": s.get("source")}
            for s in scenarios if s["type"] == "ANALYST_TARGET" and s["figures"].get("target_low")
        ]

        b = _cached_baseline(score.ticker, as_of)
        payload["baseline"] = _baseline_view(b)
        if not b.supported:
            payload.update(available=False, reason=b.reason)
            return Response(payload)

        checked_raw = request.query_params.get("checked", "").strip()
        checked = sorted({int(x) for x in checked_raw.split(",") if x.strip().isdigit()}) if checked_raw else []
        chosen_idx = [i for i in checked if i < len(current.scenarios) and scenarios[i]["checkable"]]
        chosen = [current.scenarios[i] for i in chosen_idx]
        levers_auto, notes = apply_scenarios(b, chosen)

        levers = dict(levers_auto)
        overridden, override_values = [], {}
        for k in OVERRIDABLE:
            v = _parse_float(request.query_params.get(k))
            if v is not None:
                override_values[k] = v
                overridden.append(k)
        eps = _parse_float(request.query_params.get("eps"))
        if eps is not None and eps > 0:
            override_values["eps_override"] = eps
            overridden.append("eps")
        levers.update(override_values)
        if overridden:
            notes.append("已手動調整：" + "、".join(overridden))

        p = project(b, levers, notes)
        payload.update(
            available=True, checked=[i for i in checked if i < len(scenarios) and scenarios[i]["checkable"]],
            levers_auto=levers_auto, levers=levers, overridden=overridden,
            projection={
                "route": p.route, "revenue": p.revenue, "operating_income": p.operating_income, "eps": p.eps,
                "multiple": p.multiple, "target_price": p.target_price, "implied_return": p.implied_return,
            },
            notes=p.notes,
            bootstrap_percentile=_bootstrap_percentile(score.ticker, as_of, b.price, p.implied_return),
            waterfall=waterfall(b, chosen, [_waterfall_label(scenarios[i]) for i in chosen_idx], override_values),
        )
        return Response(payload)
