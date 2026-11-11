# scripts/backtest_thesis_scenarios.py
"""
論點情境試算的實證回測。2026-10-27 改成跟現在的試算同一套方法(以現在為基準、試算期間依節目回測期間、
看多／看空分組)；舊版(勾了就跳到歷史 P75、固定 12 個月、支持／反對分組)見 git 81d7819。

一、財務方向：勾了情境，之後的財報真的比「現在」更好(或更差)嗎？
    以節目發布日為基準，取當時已公告的近四季(TTM)，再取「一年後」的近四季當結果：
      - 營收類情境：實際營收年增率是否高於(或低於)發布當時的年增率——試算「都不勾」就是延續目前年增率，
        所以這正是在檢驗「勾了看多就往上加」有沒有道理
      - 毛利率類：實際毛利率是否比基準時上升(或下降)
      - 費用率類(成本下降中的精簡費用)：實際營業費用率是否下降
    對照組：同一批「股票＋發布日」不分情境的比例；命中率要比對照組高，才代表情境帶有資訊。
    另外比較幅度：實際偏離現況多少 vs 試算一次調多少(一般波動幅度)。

二、估值：試算期間(節目回測期間)結束時的實際股價，跟哪一種目標價比較接近？
      A 維持現況(報酬 0)
      B 都不勾(延續目前成長)
      B5 都不勾，但營收成長改用過去 5 年中位數；B½ 兩者各一半(第 11 點：成長率要不要回到長期平均)
      M 依論點方向勾選(看多論點勾全部看多情境、看空論點勾全部看空情境，跟畫面的按鈕同一套規則)
      M2 同 M，但不使用節目直接給的 EPS/本益比數字
    指標：報酬的絕對誤差中位數、方向命中率、等級相關(Spearman)；另外把誤差拆成 EPS 與本益比兩部分。

三、暫定幅度的情境(第 8 點)：本益比類(估值、總經、法規風險)和財務風險，實際上本益比/EPS 變了多少，
    跟試算調的幅度、跟對照組比。

防止偷看未來：基準只用 disclosure_date <= 發布日的財報；歷史分布只用基準日以前的季度；
股價用發布日(含)以前最近一個交易日的收盤。股價與 EPS 都換到現在的股數基準(已還原分割)。

已知限制：節目全部集中在 2025-04 之後，等於只有一段 AI 多頭行情；同一家公司重複出現很多次
(台積電、輝達佔一大半)，不是獨立樣本；結果只能說明這段期間的表現。

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
    GROWTH_CLAMP, _price_on, _step, apply_scenarios, build_baseline, eps_year_fits, project,
    project_scenarios, scenario_levers, ttm_series,
)
from apps.summaries.models import FinancialReportCache, ThesisScenarioExtraction
from apps.summaries.services.thesis_scenario import PROMPT_VERSION

TODAY = date.today()
DEFAULT_MONTHS = 12
MONTHS_RANGE = (1, 36)
OUT_DIR = "out"
PE_TYPES = ("RERATING_UP", "RERATING_DOWN", "MACRO_EASING", "MACRO_TIGHTENING", "REGULATORY_RISK")


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


def checkable(s: dict) -> bool:
    """跟畫面一樣(thesis_views._is_checkable)；第三部分的本益比統計另外看 PE_TYPES，不受這裡影響。"""
    from apps.calculator.thesis_views import _is_checkable
    return _is_checkable(s)


def side_preset(scenarios: list, side: str, b, months: int) -> list:
    """跟畫面「看多情境／看空情境」按鈕同一套規則：可勾選、同一邊，帶數字的 EPS/本益比各只取第一個，
    年份不符的 EPS 不能勾。"""
    picked, used = [], set()
    for s in scenarios:
        f = s.get("figures") or {}
        if not checkable(s) or (("down" if s.get("effect") == "down" else "up") != side):
            continue
        if s.get("type") == "EPS_FORECAST" and not eps_year_fits(b, f.get("year"), months):
            continue
        group = "eps" if s["type"] == "EPS_FORECAST" else \
            "pe" if s["type"] in ("RERATING_UP", "RERATING_DOWN") and f.get("pe") else None
        if group and group in used:
            continue
        if group:
            used.add(group)
        picked.append(s)
    return picked


def without_figures(scenarios: list) -> list:
    return [s for s in scenarios if not (s.get("type") == "EPS_FORECAST"
                                         or (s.get("type") in ("RERATING_UP", "RERATING_DOWN") and (s.get("figures") or {}).get("pe")))]


def horizon_months(record) -> int:
    """跟 thesis_views 一樣：驗證句的起訖日，沒有就 12 個月。"""
    if record.start_time and record.end_time and record.end_time > record.start_time:
        m = round((record.end_time - record.start_time).days / 30)
    else:
        m = DEFAULT_MONTHS
    return min(max(m, MONTHS_RANGE[0]), MONTHS_RANGE[1])


def ttm_at(series: list, end: date, tol: int = 25):
    """結束日最接近 end(差距 tol 天內)的近四季。"""
    best = min(series, key=lambda x: abs((x["end"] - end).days), default=None)
    return best if best and abs((best["end"] - end).days) <= tol else None


def opex_ratio(t):
    if t["revenue"] and t["gross_profit"] is not None and t["operating_income"] is not None:
        return (t["gross_profit"] - t["operating_income"]) / t["revenue"]
    return None


def rate(hits: list) -> str:
    return f"{np.mean(hits) * 100:5.1f}%（n={len(hits)}）" if hits else "   —   （n=0）"


def pct(x) -> str:
    return "  —  " if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.1f}%"


def main():
    rows = list(
        ThesisScenarioExtraction.objects.using("summariesdb")
        .filter(prompt_version=PROMPT_VERSION, status="ok", summary__published_at__isnull=False)
        .select_related("summary", "backtest_record")
        .only("ticker", "scenarios", "summary__published_at", "backtest_record__thesis",
              "backtest_record__direction", "backtest_record__start_time", "backtest_record__end_time",
              "backtest_record__timeframe_raw")
    )
    by_ticker = defaultdict(list)
    for r in rows:
        by_ticker[r.ticker].append(r)
    print(f"論點 {len(rows)} 筆，{len(by_ticker)} 檔股票；載入財報與股價…")

    fin_records, val_records = [], []
    control = {}   # (ticker, as_of) → 不分情境的結果

    for n, (ticker, recs) in enumerate(sorted(by_ticker.items()), 1):
        fin_rows, prices = load_ticker_data(ticker)
        if not fin_rows or not prices:
            continue
        full_series = ttm_series(fin_rows, TODAY)
        seen_fin, seen_val = set(), set()
        for r in recs:
            as_of = r.summary.published_at.date()
            p0 = _price_on(prices, as_of)
            if not p0:
                continue
            b = build_baseline(ticker, fin_rows, p0, as_of, prices=prices)
            if not b.supported or not b.route:
                continue

            # ── 一、財務方向(一年後的近四季 vs 發布當時) ──
            series = ttm_series(fin_rows, as_of)
            base = series[0] if series else None
            outcome = ttm_at(full_series, base["end"] + timedelta(days=365)) if base else None
            if outcome and base["revenue"] and outcome["revenue"] and b.revenue_growth is not None:
                actual_growth = outcome["revenue"] / base["revenue"] - 1
                cur_growth = b.revenue_growth
                gm = lambda t: t["gross_profit"] / t["revenue"] if t["gross_profit"] is not None else None
                d_gm = gm(outcome) - gm(base) if gm(base) is not None and gm(outcome) is not None else None
                ox0, ox1 = opex_ratio(base), opex_ratio(outcome)
                d_ox = ox1 - ox0 if ox0 is not None and ox1 is not None else None
                actual = {"revenue_growth": actual_growth - cur_growth, "gross_margin": d_gm, "opex_ratio": d_ox}
                control[(ticker, as_of)] = actual
                for s in r.scenarios:
                    key = (ticker, as_of, s["type"], s.get("cost_kind"), s.get("margin_drag"))
                    if key in seen_fin or not checkable(s):
                        continue
                    seen_fin.add(key)
                    for lever, direction in scenario_levers(s).items():
                        if lever not in actual or actual[lever] is None:
                            continue
                        fin_records.append({
                            "ticker": ticker, "as_of": as_of.isoformat(), "type": s["type"], "lever": lever,
                            "direction": direction, "effect": s.get("effect"), "actual": actual[lever],
                            "step": _step(b, lever, direction),
                            "hit": (actual[lever] > 0) == (direction > 0),
                        })

            # ── 二、估值(試算期間 = 節目回測期間) ──
            months = horizon_months(r.backtest_record)
            end = as_of + timedelta(days=round(months * 30.44))
            thesis_key = (as_of, r.backtest_record.thesis.strip())
            if end > TODAY or thesis_key in seen_val:
                continue
            seen_val.add(thesis_key)
            p1 = _price_on(prices, end)
            if not p1:
                continue
            side = "down" if r.backtest_record.direction == "bearish" else "up"
            preset = side_preset(r.scenarios, side, b, months)
            proj = {"B": project_scenarios(b, [], months),
                    "M": project_scenarios(b, preset, months),
                    "M2": project_scenarios(b, without_figures(preset), months)}
            p50 = (b.history.get("revenue_growth") or {}).get("p50")
            if p50 is not None and b.revenue_growth is not None:
                for key, g in (("B5", p50), ("Bh", (p50 + b.revenue_growth) / 2)):
                    levers, notes = apply_scenarios(b, [], months)
                    levers["revenue_growth"] = min(max(g, GROWTH_CLAMP[0]), GROWTH_CLAMP[1])
                    proj[key] = project(b, levers, notes, months)
            # 單一情境的試算本益比變化(第三部分用)
            pe_model = {}
            for s in r.scenarios:
                if s["type"] in PE_TYPES and checkable(s) and s["type"] not in pe_model:
                    one = project_scenarios(b, [s], months)
                    if one and one.multiple and proj["B"] and proj["B"].multiple:
                        pe_model[s["type"]] = one.multiple / proj["B"].multiple - 1
            out = ttm_at(full_series, b.ttm_end + timedelta(days=round(months * 30.44))) if b.ttm_end else None
            out_eps = out["eps"] if out else None
            val_records.append({
                "ticker": ticker, "as_of": as_of.isoformat(), "months": months, "side": side,
                "route": b.route, "actual": p1 / p0 - 1, "n_preset": len(preset),
                "types": sorted({s["type"] for s in r.scenarios if checkable(s)}),
                "pe_model": pe_model, "base_eps": b.eps, "actual_eps": out_eps, "pe0": b.pe,
                "pe1": p1 / out_eps if out_eps and out_eps > 0 else None,
                **{k: (p.implied_return if p else None) for k, p in proj.items()},
                **{k + "_eps": (p.eps if p else None) for k, p in proj.items()},
            })
        print(f"  [{n}/{len(by_ticker)}] {ticker}", flush=True)

    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump({"financial": fin_records, "valuation": val_records}, open(f"{OUT_DIR}/thesis_backtest_v2.json", "w"),
              ensure_ascii=False, default=str)
    report(fin_records, val_records, control)


def report(fin, val, control):
    from scipy.stats import spearmanr

    print("\n" + "=" * 70)
    print("一、財務方向（一年後的實際財報 vs 發布當時）")
    print("=" * 70)
    labels = {"revenue_growth": ("營收年增率高於發布當時", "年增率比當時多"),
              "gross_margin": ("毛利率上升", "毛利率變化"),
              "opex_ratio": ("營業費用率上升", "費用率變化")}
    for lever, (label, mag_label) in labels.items():
        ctrl = [v[lever] for v in control.values() if v.get(lever) is not None]
        recs = [r for r in fin if r["lever"] == lever]
        print(f"\n【{label}】對照組 {rate([c > 0 for c in ctrl])}，{mag_label}中位數 {pct(np.median(ctrl) if ctrl else None)}")
        for direction, name in ((+1, "預期↑"), (-1, "預期↓")):
            sub = [r for r in recs if r["direction"] == direction]
            if not sub:
                continue
            hits = [r["hit"] for r in sub]
            print(f"  {name}的情境：命中 {rate(hits)}；實際{mag_label}中位數 {pct(np.median([r['actual'] for r in sub]))}，"
                  f"試算一次調 {pct(np.median([r['step'] for r in sub if r['step'] is not None]))}")
        by_type = defaultdict(list)
        for r in recs:
            by_type[(r["type"], r["direction"])].append(r)
        for (t, d), sub in sorted(by_type.items(), key=lambda x: -len(x[1])):
            if len(sub) >= 5:
                print(f"    {t:20} {'↑' if d > 0 else '↓'}  命中 {rate([r['hit'] for r in sub])}"
                      f"　實際 {pct(np.median([r['actual'] for r in sub]))}　試算 {pct(np.median([r['step'] for r in sub if r['step'] is not None]))}")

    print("\n" + "=" * 70)
    print("二、估值（試算期間 = 節目回測期間，期末實際報酬 vs 試算報酬）")
    print("=" * 70)
    actual = np.array([v["actual"] for v in val])
    print(f"樣本 {len(val)} 句論點；試算期間 {dict(sorted(Counter(v['months'] for v in val).items()))}（月：句數）")
    print(f"實際報酬中位數 {np.median(actual) * 100:.1f}%，上漲比例 {np.mean(actual > 0) * 100:.1f}%；"
          f"計算路線 {dict(Counter(v['route'] for v in val))}")

    def metrics(key, subset=None):
        base = subset if subset is not None else val
        vs = base if key == "A" else [v for v in base if v.get(key) is not None]
        if not vs:
            return 0, float("nan"), float("nan"), float("nan")
        a = np.array([v["actual"] for v in vs])
        p = np.zeros(len(vs)) if key == "A" else np.array([v[key] for v in vs])
        mae = np.median(np.abs(p - a)) * 100
        direction = np.mean((p > 0) == (a > 0)) * 100 if key != "A" else float("nan")
        rho = spearmanr(p, a).statistic if key != "A" and len(vs) > 3 else float("nan")
        return len(vs), mae, direction, rho

    models = (("A", "A 維持現況"), ("B", "B 都不勾（延續目前成長）"), ("B5", "B5 成長改用5年中位數"),
              ("Bh", "B½ 兩者各半"), ("M", "M 依論點方向勾選"), ("M2", "M2 同上但不用節目數字"))
    for title, subset in (("全部", None), ("看多論點", [v for v in val if v["side"] == "up"]),
                          ("看空論點", [v for v in val if v["side"] == "down"])):
        print(f"\n〔{title}〕{'':18} {'n':>4} {'誤差中位數':>9} {'方向命中':>8} {'等級相關':>8}")
        for key, label in models:
            k, mae, d, rho = metrics(key, subset)
            if k:
                print(f"  {label:24} {k:4d} {mae:8.1f}% {d:7.1f}% {rho:8.2f}")

    eps_rows = [v for v in val if v.get("actual_eps") and v.get("base_eps") and v["base_eps"] > 0
                and v["actual_eps"] > 0 and v["route"] != "ps"]
    if eps_rows:
        print(f"\n拆解（{len(eps_rows)} 句有期末的實際近四季 EPS）：")
        print(f"  實際 EPS 成長中位數 {np.median([v['actual_eps'] / v['base_eps'] - 1 for v in eps_rows]) * 100:.1f}%")
        for key, label in (("base", "EPS 維持不變"), ("B", "B 延續目前成長"), ("B5", "B5 5年中位數"),
                           ("Bh", "B½ 兩者各半"), ("M", "M 依論點方向"), ("M2", "M2 不用節目數字")):
            errs = [abs((v["base_eps"] if key == "base" else v[key + "_eps"]) - v["actual_eps"]) / v["actual_eps"] * 100
                    for v in eps_rows if key == "base" or v.get(key + "_eps") is not None]
            if errs:
                print(f"  {label:16} EPS 誤差中位數 {np.median(errs):5.1f}%（n={len(errs)}）")
        pe_change = [v["pe1"] / v["pe0"] - 1 for v in eps_rows if v.get("pe1") and v.get("pe0")]
        print(f"  本益比期末的變化中位數 {np.median(pe_change) * 100:+.1f}%（上升比例 {np.mean(np.array(pe_change) > 0) * 100:.0f}%）")

    first = {}
    for v in val:
        first.setdefault((v["ticker"], v["as_of"][:7]), v)
    dedup = list(first.values())
    print(f"\n去重後（每檔股票每月只留一句，{len(dedup)} 句）：")
    for key, label in models:
        k, mae, d, rho = metrics(key, dedup)
        if k:
            print(f"  {label:24} 誤差中位數 {mae:5.1f}%　方向命中 {d:5.1f}%　等級相關 {rho:5.2f}")

    print("\n" + "=" * 70)
    print("三、暫定幅度的情境：實際變化 vs 試算調整")
    print("=" * 70)
    pe_rows = [v for v in eps_rows if v.get("pe1") and v.get("pe0")]
    ctrl_pe = [v["pe1"] / v["pe0"] - 1 for v in pe_rows]
    print(f"對照組（全部論點）本益比變化中位數 {pct(np.median(ctrl_pe) if ctrl_pe else None)}（n={len(ctrl_pe)}）")
    for t in PE_TYPES:
        sub = [v for v in pe_rows if t in v["types"]]
        if not sub:
            print(f"  {t:18} 沒有樣本")
            continue
        real = [v["pe1"] / v["pe0"] - 1 for v in sub]
        model = [v["pe_model"][t] for v in sub if t in v["pe_model"]]
        print(f"  {t:18} n={len(sub):3d}　實際本益比變化中位數 {pct(np.median(real))}"
              f"（比對照組 {pct(np.median(real) - np.median(ctrl_pe))}）　試算調 {pct(np.median(model) if model else None)}")
    ctrl_eps = [v["actual_eps"] / v["base_eps"] - 1 for v in eps_rows]
    sub = [v for v in eps_rows if "FINANCIAL_RISK" in v["types"]]
    if sub:
        real = [v["actual_eps"] / v["base_eps"] - 1 for v in sub]
        print(f"  FINANCIAL_RISK     n={len(sub):3d}　實際 EPS 成長中位數 {pct(np.median(real))}"
              f"（對照組 {pct(np.median(ctrl_eps))}）　試算打 95 折")
    else:
        print("  FINANCIAL_RISK     沒有樣本")


if __name__ == "__main__":
    main()
