# scripts/backtest_thesis_scenarios.py
"""
論點情境試算的實證回測(2026-10-26)。回答兩個問題：

一、財務方向回測：情境說的方向，之後的財報真的這樣變了嗎？
    以節目發布日為基準，取當時已公告的近四季(TTM)，再取「再往後四季」的近四季當結果：
      - 營收類情境(需求增加、漲價…)：實際營收年增率是否高於(或低於)這家公司當時的歷史中位數(P50)
        ——試算的「一般趨勢」就是用 P50，所以這正是在檢驗「勾了利多就往 P75 調」有沒有道理
      - 毛利類情境(漲價、組合升級、成本上升…)：實際毛利率是否比基準時上升(或下降)
    對照組：同一批「股票＋發布日」不分情境的比例(例如這段期間本來就有 60% 的公司營收成長高於中位數)，
    命中率要比對照組高，才代表情境帶有資訊，而不只是搭上大環境。

二、估值回測：12 個月後的實際股價，跟哪一種目標價比較接近？
      A 維持現況(報酬 0)
      B 公司一般趨勢(都不勾：營收用歷史 P50)
      C 節目的論點(勾選全部支持的情境，跟畫面「節目的論點」按鈕同一套規則)
      C2 節目的論點，但不使用節目直接給的 EPS/本益比數字(只用情境推參數)
    指標：報酬的絕對誤差中位數、方向命中率(預估報酬 > 0 時實際是否也 > 0)、預估與實際的等級相關(Spearman)。

防止偷看未來：基準只用 disclosure_date <= 發布日的財報；歷史分布只用基準日以前的季度；
股價用發布日(含)以前最近一個交易日的收盤。股價與 EPS 都換到現在的股數基準(已還原分割)，
這是單位換算，不是用到未來的資訊。

已知限制：節目全部集中在 2025-04 之後，等於只有一段 AI 多頭行情；樣本內同一家公司重複出現很多次
(台積電、輝達佔一大半)，不是獨立樣本；結果只能說明這段期間的表現，不能推論到其他市況。

用法：python scripts/backtest_thesis_scenarios.py
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

import numpy as np

from apps.calculator.services.thesis_valuation import (
    _price_on, build_baseline, history_distributions, project_scenarios, scenario_levers, ttm_series,
)
from apps.summaries.models import FinancialReportCache, ThesisScenarioExtraction

TODAY = date.today()
HORIZON_DAYS = 365
OUT_DIR = "out"


def load_ticker_data(ticker: str):
    """財報列(FinMind EPS 換到現在股數基準) + 全部日收盤價(已還原分割、未還原股息)。"""
    import yfinance as yf
    from apps.calculator.services.implied_assumptions import _split_ratios

    rows = list(FinancialReportCache.objects.using("summariesdb").filter(ticker=ticker).values(
        "fiscal_period_end", "disclosure_date", "data_source",
        "revenue", "gross_profit", "operating_income", "net_income", "eps"))
    if any(r["data_source"] == "finmind" for r in rows):
        for sd, ratio in _split_ratios(ticker):
            for r in rows:
                if r["data_source"] == "finmind" and r["eps"] is not None and r["disclosure_date"] \
                        and sd > r["disclosure_date"]:
                    r["eps"] = r["eps"] / ratio
    try:
        hist = yf.Ticker(ticker).history(start="2018-01-01", end=(TODAY + timedelta(days=1)).isoformat(),
                                         interval="1d", auto_adjust=False)["Close"].dropna()
        prices = {ts.date(): float(v) for ts, v in hist.items()}
    except Exception:
        prices = {}
    return rows, prices


def ui_support_preset(scenarios: list) -> list:
    """跟畫面「節目的論點」按鈕同一套規則：可勾選、支持論點，帶數字的 EPS/本益比各只取第一個。"""
    picked, used = [], set()
    for s in scenarios:
        t, f = s.get("type"), s.get("figures") or {}
        if t in ("TECHNICAL_FLOW", "ANALYST_TARGET") or (t == "EPS_FORECAST" and not f.get("eps")):
            continue
        if s.get("stance") != "support":
            continue
        group = "eps" if t == "EPS_FORECAST" else "pe" if t in ("RERATING_UP", "RERATING_DOWN") and f.get("pe") else None
        if group and group in used:
            continue
        if group:
            used.add(group)
        picked.append(s)
    return picked


def without_figures(scenarios: list) -> list:
    return [s for s in scenarios if not (s.get("type") == "EPS_FORECAST"
                                         or (s.get("type") in ("RERATING_UP", "RERATING_DOWN") and (s.get("figures") or {}).get("pe")))]


def rate(hits: list) -> str:
    return f"{np.mean(hits) * 100:5.1f}%（n={len(hits)}）" if hits else "   —   （n=0）"


def main():
    rows = list(
        ThesisScenarioExtraction.objects.using("summariesdb")
        .filter(prompt_version="v5", status="ok", summary__published_at__isnull=False)
        .select_related("summary", "backtest_record")
        .only("ticker", "scenarios", "summary__published_at", "backtest_record__thesis", "backtest_record__direction")
    )
    by_ticker = defaultdict(list)
    for r in rows:
        by_ticker[r.ticker].append(r)
    print(f"論點 {len(rows)} 筆，{len(by_ticker)} 檔股票；載入財報與股價…")

    fin_records, val_records = [], []
    control = {}   # (ticker, as_of) → {"growth_above_p50": bool, "gm_up": bool}

    for n, (ticker, recs) in enumerate(sorted(by_ticker.items()), 1):
        fin_rows, prices = load_ticker_data(ticker)
        if not fin_rows:
            continue
        full_series = ttm_series(fin_rows, TODAY)
        seen_fin, seen_val = set(), set()
        for r in recs:
            as_of = r.summary.published_at.date()

            # ── 一、財務方向 ──
            series = ttm_series(fin_rows, as_of)
            if series:
                base = series[0]
                outcome = next((s for s in full_series if 350 <= (s["end"] - base["end"]).days <= 380), None)
                hist = history_distributions(series)
                p50 = hist.get("revenue_growth", {}).get("p50")
                if outcome and base["revenue"] and outcome["revenue"] and p50 is not None:
                    actual_growth = outcome["revenue"] / base["revenue"] - 1
                    gm_base = base["gross_profit"] / base["revenue"] if base["gross_profit"] is not None else None
                    gm_out = outcome["gross_profit"] / outcome["revenue"] if outcome["gross_profit"] is not None else None
                    d_gm = gm_out - gm_base if gm_base is not None and gm_out is not None else None
                    control[(ticker, as_of)] = {"growth_above_p50": actual_growth > p50,
                                                "gm_up": None if d_gm is None else d_gm > 0}
                    for s in r.scenarios:
                        key = (ticker, as_of, s["type"])
                        if key in seen_fin:
                            continue
                        seen_fin.add(key)
                        for lever, direction in scenario_levers(s).items():
                            if lever == "revenue_growth":
                                fin_records.append({"ticker": ticker, "as_of": as_of.isoformat(), "type": s["type"],
                                                    "lever": lever, "direction": direction, "stance": s.get("stance"),
                                                    "actual": actual_growth, "base": p50,
                                                    "hit": (actual_growth > p50) == (direction > 0)})
                            elif lever == "gross_margin" and d_gm is not None:
                                fin_records.append({"ticker": ticker, "as_of": as_of.isoformat(), "type": s["type"],
                                                    "lever": lever, "direction": direction, "stance": s.get("stance"),
                                                    "actual": d_gm, "base": 0.0,
                                                    "hit": (d_gm > 0) == (direction > 0)})

            # ── 二、估值 ──
            thesis_key = (as_of, r.backtest_record.thesis.strip())
            if as_of > TODAY - timedelta(days=HORIZON_DAYS) or thesis_key in seen_val or not prices:
                continue
            seen_val.add(thesis_key)
            p0 = _price_on(prices, as_of)
            p1 = _price_on(prices, as_of + timedelta(days=HORIZON_DAYS))
            if not p0 or not p1:
                continue
            b = build_baseline(ticker, fin_rows, p0, as_of, prices=prices)
            if not b.supported or not b.route:
                continue
            preset = ui_support_preset(r.scenarios)
            proj = {
                "B": project_scenarios(b, []),
                "C": project_scenarios(b, preset),
                "C2": project_scenarios(b, without_figures(preset)),
            }
            eps_year_mismatch = any(
                s.get("type") == "EPS_FORECAST" and (s.get("figures") or {}).get("year")
                and s["figures"]["year"] not in (as_of.year, as_of.year + 1)
                for s in preset)
            # 拆解誤差：12 個月後「實際的近四季 EPS」(期末約在基準近四季之後一年) 與當時本益比的變化
            out_eps = next((x["eps"] for x in full_series if b.ttm_end and 350 <= (x["end"] - b.ttm_end).days <= 380), None)
            val_records.append({
                "ticker": ticker, "as_of": as_of.isoformat(), "direction": r.backtest_record.direction,
                "route": b.route, "actual": p1 / p0 - 1, "eps_year_mismatch": eps_year_mismatch,
                "n_preset": len(preset), "base_eps": b.eps, "actual_eps": out_eps, "pe0": b.pe,
                "pe1": p1 / out_eps if out_eps and out_eps > 0 else None,
                **{k: (p.implied_return if p else None) for k, p in proj.items()},
                **{k + "_eps": (p.eps if p else None) for k, p in proj.items()},
            })
        print(f"  [{n}/{len(by_ticker)}] {ticker}")

    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump({"financial": fin_records, "valuation": val_records}, open(f"{OUT_DIR}/thesis_backtest.json", "w"),
              ensure_ascii=False, default=str)
    report(fin_records, val_records, control)


def report(fin, val, control):
    print("\n" + "=" * 70)
    print("一、財務方向回測（發布後四季的實際財報）")
    print("=" * 70)
    ctrl_g = [v["growth_above_p50"] for v in control.values()]
    ctrl_m = [v["gm_up"] for v in control.values() if v["gm_up"] is not None]
    print(f"對照組（同一批股票＋發布日，不分情境）：營收成長高於歷史中位數 {rate(ctrl_g)}；毛利率上升 {rate(ctrl_m)}")
    for lever, label in (("revenue_growth", "營收成長高於中位數"), ("gross_margin", "毛利率上升")):
        recs = [r for r in fin if r["lever"] == lever]
        up = [r["hit"] for r in recs if r["direction"] > 0]
        down = [r["hit"] for r in recs if r["direction"] < 0]
        print(f"\n【{label}】")
        print(f"  利多情境（預期↑）命中 {rate(up)}")
        print(f"  利空情境（預期↓）命中 {rate(down)}　← 命中＝實際沒有{label}")
        for stance, label2 in (("support", "節目主張的"), ("against", "節目提到的風險／反方")):
            u = [r["hit"] for r in recs if r["direction"] > 0 and r.get("stance") == stance]
            dn = [r["hit"] for r in recs if r["direction"] < 0 and r.get("stance") == stance]
            print(f"    {label2}：利多命中 {rate(u)}；利空命中 {rate(dn)}")
        by_type = defaultdict(list)
        for r in recs:
            by_type[(r["type"], r["direction"])].append(r["hit"])
        for (t, d), hits in sorted(by_type.items(), key=lambda x: -len(x[1])):
            if len(hits) >= 5:
                print(f"    {t:20} {'↑' if d > 0 else '↓'}  命中 {rate(hits)}")

    print("\n" + "=" * 70)
    print("二、估值回測（12 個月後實際報酬 vs 試算報酬）")
    print("=" * 70)
    actual = np.array([v["actual"] for v in val])
    print(f"樣本 {len(val)} 句論點；12 個月實際報酬中位數 {np.median(actual) * 100:.1f}%，"
          f"上漲比例 {np.mean(actual > 0) * 100:.1f}%")
    print(f"計算路線：{dict(Counter(v['route'] for v in val))}")
    from scipy.stats import spearmanr

    def metrics(key, subset=None):
        vs = [v for v in (subset or val) if v.get(key) is not None] if key != "A" else (subset or val)
        a = np.array([v["actual"] for v in vs])
        p = np.zeros(len(vs)) if key == "A" else np.array([v[key] for v in vs])
        mae = np.median(np.abs(p - a)) * 100
        direction = np.mean((p > 0) == (a > 0)) * 100 if key != "A" else float("nan")
        rho = spearmanr(p, a).statistic if key != "A" and len(vs) > 3 else float("nan")
        return len(vs), mae, direction, rho

    print(f"\n{'目標價':24} {'n':>4} {'絕對誤差中位數':>12} {'方向命中':>8} {'等級相關':>8}")
    for key, label in (("A", "A 維持現況"), ("B", "B 公司一般趨勢"), ("C", "C 節目的論點"),
                       ("C2", "C2 節目論點（不用節目數字）")):
        n, mae, d, rho = metrics(key)
        print(f"{label:24} {n:4d} {mae:11.1f}% {d:7.1f}% {rho:8.2f}")
    mism = [v for v in val if v["eps_year_mismatch"]]
    if mism:
        n, mae, d, rho = metrics("C", mism)
        print(f"\n其中 C 用到年份不符的 EPS（不是發布當年或隔年）：{n} 句，絕對誤差中位數 {mae:.1f}%")
    # 誤差來自 EPS 還是本益比
    eps_rows = [v for v in val if v.get("actual_eps") and v.get("base_eps") and v["base_eps"] > 0 and v["route"] != "ps"]
    if eps_rows:
        def ape(pred, act):
            return abs(pred - act) / abs(act) * 100
        print(f"\n拆解（{len(eps_rows)} 句有一年後的實際近四季 EPS）：")
        print(f"  實際 EPS 成長中位數 {np.median([v['actual_eps'] / v['base_eps'] - 1 for v in eps_rows]) * 100:.1f}%")
        for key, label in (("base", "EPS 維持不變"), ("B", "B 一般趨勢"), ("C", "C 節目的論點"), ("C2", "C2 不用節目數字")):
            errs = [ape(v["base_eps"] if key == "base" else v[key + "_eps"], v["actual_eps"])
                    for v in eps_rows if (key == "base" or v.get(key + "_eps") is not None) and v["actual_eps"] > 0]
            print(f"  {label:16} EPS 誤差中位數 {np.median(errs):5.1f}%（n={len(errs)}）")
        pe_change = [v["pe1"] / v["pe0"] - 1 for v in eps_rows if v.get("pe1") and v.get("pe0")]
        print(f"  本益比一年後的變化中位數 {np.median(pe_change) * 100:+.1f}%（上升比例 {np.mean(np.array(pe_change) > 0) * 100:.0f}%）")
    # 每檔股票只算一次，避免台積電、輝達的重複樣本主導結果
    first = {}
    for v in val:
        first.setdefault((v["ticker"], v["as_of"][:7]), v)
    dedup = list(first.values())
    print(f"\n去重後（每檔股票每月只留一句，{len(dedup)} 句）：")
    for key, label in (("A", "A 維持現況"), ("B", "B 公司一般趨勢"), ("C", "C 節目的論點")):
        n, mae, d, rho = metrics(key, dedup)
        print(f"  {label:16} 絕對誤差中位數 {mae:5.1f}%　方向命中 {d:5.1f}%　等級相關 {rho:5.2f}")
    bull = [v["actual"] > 0 for v in val if v["direction"] == "bullish"]
    bear = [v["actual"] < 0 for v in val if v["direction"] == "bearish"]
    print(f"\n參考：節目本身的方向判斷 —— 看多後真的上漲 {rate(bull)}；看空後真的下跌 {rate(bear)}")


if __name__ == "__main__":
    main()
