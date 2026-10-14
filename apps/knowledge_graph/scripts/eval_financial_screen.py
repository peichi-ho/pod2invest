"""
驗證財務篩選（Layer 2）：通過篩選的公司，之後的財務與風險，是不是真的比被淘汰的好？

【為什麼不能直接比分數】篩選本來就是留下高分的，拿分數或通過項目數當成績是循環論證。
這裡的成績全部是「查詢日（as-of）當下看不到、之後才發生的事」：
  主要成績：之後兩季內有沒有出現「惡化事件」——任一季營業利益為負，或 EPS 比去年同期少超過 30%
            （去年同期 EPS 為正才算）。事先固定的定義，不看結果調整。
  次要成績：之後 6 個月（126 個交易日）最大回撤是否達 -30% 以上。
  附加成績：之後 6 個月相對加權指數的超額報酬（只當參考）。

【測的是你實際寫的篩選程式】直接呼叫 fundamental_score.evaluate_candidate()，只把
financial_data.get_financial_history() 換成「只用 as-of 當天已公告的財報」的版本：
  - 不寫入正式資料庫（資料從 FinMind 抓到本機快取）
  - 年營收（三年 CAGR 那項）只用 as-of 當天已完整公告的會計年度，修掉回溯時偷看未來的問題
  - FinMind 的營業現金流是「累計值」（Q2=上半年合計），預設還原成單季值再餵給篩選；
    --ocf asis 則保留累計值（等同正式程式碼遇到 FinMind 來源資料時的行為），用來比較這個問題的影響
資料限制：歷史財報只補得到台股（FinMind）；美股在回溯時視為「無法評估」，不納入比較。

用法（專案根目錄）：
    python apps/knowledge_graph/scripts/eval_financial_screen.py --stage fetch
    python apps/knowledge_graph/scripts/eval_financial_screen.py --stage screen
    python apps/knowledge_graph/scripts/eval_financial_screen.py --stage analyze
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pickle
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_graph_vs_random as E   # 共用查詢日、籃子、股價；匯入時會 django.setup()

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
FIN_START, FIN_END = "2021-01-01", "2026-10-14"
TIERS = ["保守", "均衡", "積極"]
HEADLINE_TIER = "均衡"
FWD_QUARTERS = 2
FWD_DAYS = 126                  # 約 6 個月的交易日
DD_EVENT = -0.30
EPS_DROP = 0.7                  # EPS 低於去年同期的 70% 視為大幅下滑
LAST_DATE_FUNDAMENTAL = dt.date(2026, 5, 1)   # 之後兩季的財報要已經公告（資料到 2026Q2）
LAST_DATE_PRICE = dt.date(2026, 4, 1)         # 之後 6 個月的股價要已經發生
N_DROPS = 1000


# ═════════════════════════════════════════════════════════════════════════════
# fetch：從 FinMind 補抓台股歷史財報到本機快取
# ═════════════════════════════════════════════════════════════════════════════

def finmind_get(dataset: str, stock_id: str, tries: int = 8, max_limit_waits: int = 80) -> list[dict]:
    """
    失敗會重試；遇到限流（免費額度約每小時 300 次）每 90 秒再試一次，直到額度重置。
    成功但沒有資料回傳 []；一直失敗才丟例外。
    """
    k = waits = 0
    while k < tries:
        try:
            r = requests.get(FINMIND_URL, timeout=30, params={
                "dataset": dataset, "data_id": stock_id, "start_date": FIN_START, "end_date": FIN_END})
            j = r.json()
            if r.status_code == 200 and j.get("status") == 200:
                return j.get("data", [])
            msg = str(j.get("msg", "")).lower()
            if r.status_code in (402, 429) or "limit" in msg:
                waits += 1
                if waits > max_limit_waits:
                    break
                if waits % 10 == 1:
                    E.log(f"  [finmind] 限流（{dataset} {stock_id}），每 90 秒再試，已等 {waits} 次")
                time.sleep(90)
                continue
        except Exception:
            pass
        k += 1
        time.sleep(2 * k)
    raise RuntimeError(f"FinMind 一直失敗: {dataset} {stock_id}")


def _pivot(rows: list[dict]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["date"], {})[r["type"]] = r["value"]
    return out


def stage_fetch(cache: Path):
    meta = E.jload(cache / "node_meta.json")
    R = E.load_returns(cache)
    tickers = sorted({m["ticker"] for n, m in meta.items()
                      if E.usable(n, meta, True) and m["market"] == "TW" and m["ticker"] in R.columns})
    d = cache / "fin_tw"
    d.mkdir(exist_ok=True)
    E.log(f"[fetch] 台股 {len(tickers)} 檔，每檔 3 個資料集")
    failed, t0 = [], time.time()
    for i, tk in enumerate(tickers, 1):
        f = d / f"{tk}.json"
        if f.exists():
            continue
        sid = tk.split(".")[0]
        try:
            raw = {
                "inc": _pivot(finmind_get("TaiwanStockFinancialStatements", sid)),
                "bs": _pivot(finmind_get("TaiwanStockBalanceSheet", sid)),
                "cf": _pivot(finmind_get("TaiwanStockCashFlowsStatement", sid)),
            }
            f.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            failed.append(tk)
            E.log(f"  失敗 {tk}: {e}")
        if i % 25 == 0:
            E.log(f"[fetch] {i}/{len(tickers)}，{time.time()-t0:.0f}s")
    E.log(f"[fetch] 完成，失敗 {len(failed)}：{failed}")


# ═════════════════════════════════════════════════════════════════════════════
# 把 FinMind 原始資料轉成跟正式程式碼同樣的 period 結構
# ═════════════════════════════════════════════════════════════════════════════

def _lag_days(period_end: date) -> int:
    return 90 if period_end.month == 12 else 45     # 同 financial_data._assumed_disclosure_lag_days


def build_periods(raw: dict, ocf: str) -> list[dict]:
    """欄位對照與 financial_data._fetch_finmind_periods 相同；差別只在 ocf 是否還原成單季。"""
    inc, bs, cf = raw["inc"], raw["bs"], raw["cf"]
    ocf_ytd = {}
    for pe, row in cf.items():
        v = row.get("CashFlowsFromOperatingActivities") or row.get("NetCashInflowFromOperatingActivities")
        ocf_ytd[pe] = v
    periods = []
    for pe_s, bs_row in bs.items():
        inc_row = inc.get(pe_s)
        if not inc_row or inc_row.get("Revenue") is None:
            continue
        pe = date.fromisoformat(pe_s)
        cl, ncl = bs_row.get("CurrentLiabilities"), bs_row.get("NoncurrentLiabilities")
        q_ocf = ocf_ytd.get(pe_s)
        if ocf == "fixed" and q_ocf is not None and pe.month != 3:
            prev_s = {6: f"{pe.year}-03-31", 9: f"{pe.year}-06-30", 12: f"{pe.year}-09-30"}.get(pe.month)
            prev = ocf_ytd.get(prev_s)
            q_ocf = None if prev is None else q_ocf - prev       # 缺上一季累計值就沒辦法還原，視為缺值
        periods.append({
            "fiscal_period_end": pe,
            "disclosure_date": pe + timedelta(days=_lag_days(pe)),
            "data_source": "finmind",
            "total_assets": bs_row.get("TotalAssets"),
            "total_liabilities": (cl + ncl) if cl is not None and ncl is not None else None,
            "revenue": inc_row.get("Revenue"),
            "gross_profit": inc_row.get("GrossProfit"),
            "operating_income": inc_row.get("OperatingIncome"),
            "net_income": inc_row.get("IncomeAfterTaxes"),
            "operating_cash_flow": q_ocf,
            "eps": inc_row.get("EPS"),
            "shares_outstanding": None, "price_at_period_end": None,
        })
    periods.sort(key=lambda p: p["fiscal_period_end"], reverse=True)
    return periods


def load_local(cache: Path, ocf: str) -> dict[str, list[dict]]:
    return {f.stem: build_periods(json.loads(f.read_text(encoding="utf-8")), ocf)
            for f in sorted((cache / "fin_tw").glob("*.json"))}


def make_pit_history(local: dict[str, list[dict]]):
    def pit(ticker: str, as_of: date) -> dict:
        periods = local.get(ticker, [])
        usable = [p for p in periods if p["disclosure_date"] <= as_of]
        if not usable:
            return {"data_source": None, "quarterly": [], "annual_revenue": []}
        by_year = collections.defaultdict(dict)
        for p in usable:
            by_year[p["fiscal_period_end"].year][p["fiscal_period_end"].month] = p["revenue"]
        # 只用 as-of 當天已完整公告的會計年度（四季都在）；這是對正式程式碼回溯偷看未來的修正
        annual = [{"year": y, "revenue": sum(q.values())} for y, q in by_year.items()
                  if set(q) == {3, 6, 9, 12} and all(v is not None for v in q.values())]
        annual.sort(key=lambda r: r["year"], reverse=True)
        return {"data_source": "finmind", "quarterly": usable, "annual_revenue": annual}
    return pit


# ═════════════════════════════════════════════════════════════════════════════
# screen：對每個（台股, 查詢日）跑一次正式的 evaluate_candidate，並算出之後的成績
# ═════════════════════════════════════════════════════════════════════════════

def forward_event(periods: list[dict], latest_end: date, nq: int = FWD_QUARTERS):
    """之後 nq 季的惡化事件。回傳 1/0；後面的財報還沒公告或欄位缺值回傳 None。"""
    asc = sorted(periods, key=lambda p: p["fiscal_period_end"])
    fwd = [p for p in asc if p["fiscal_period_end"] > latest_end][:nq]
    if len(fwd) < nq:
        return None
    by_end = {p["fiscal_period_end"]: p for p in asc}
    event, known = 0, 0
    for p in fwd:
        oi, eps = p.get("operating_income"), p.get("eps")
        if oi is not None:
            known += 1
            event |= int(oi < 0)
        prev = by_end.get(date(p["fiscal_period_end"].year - 1, p["fiscal_period_end"].month, p["fiscal_period_end"].day))
        if prev and prev.get("eps") is not None and prev["eps"] > 0 and eps is not None:
            known += 1
            event |= int(eps < EPS_DROP * prev["eps"])
    return event if known else None


def price_outcomes(R: pd.DataFrame, ticker: str, d: date):
    """之後 6 個月：最大回撤、相對加權指數的超額報酬。資料不足回傳 (None, None)。"""
    if ticker not in R.columns:
        return None, None
    idx = R.index[R.index > pd.Timestamp(d)][:FWD_DAYS]
    if len(idx) < FWD_DAYS:
        return None, None
    r = R.loc[idx, ticker].fillna(0.0).values
    path = np.concatenate([[1.0], np.cumprod(1 + r)])
    dd = float((path / np.maximum.accumulate(path) - 1).min())
    m = R.loc[idx, "^TWII"].fillna(0.0).values
    return dd, float(path[-1] - np.prod(1 + m))


def stage_screen(cache: Path, ocf: str, tag: str = ""):
    from apps.knowledge_graph.services import financial_data as fd
    from apps.knowledge_graph.services.fundamental_score import evaluate_candidate

    local = load_local(cache, ocf)
    fd.get_financial_history = make_pit_history(local)     # evaluate_candidate 在呼叫時才 import，會取到這個版本
    R = E.load_returns(cache)
    E.log(f"[screen] ocf={ocf}，台股 {len(local)} 檔 × {len(E.QUERY_DATES)} 個查詢日 × {len(TIERS)} 個風險等級")
    res, t0 = {}, time.time()
    for tk, periods in local.items():
        for d in E.QUERY_DATES:
            row = {"tiers": {}}
            for tier in TIERS:
                r = evaluate_candidate(tk, d, tier)
                row["tiers"][tier] = r["status"]
                if "detail" not in row:
                    row["detail"] = {k: r.get(k) for k in ("f_score", "f_score_max", "hard_gate_pass", "fiscal_period_end")}
            if row["tiers"][TIERS[0]] == "unavailable":
                row["detail"] = {}
            else:
                qs = fd.get_financial_history(tk, d)["quarterly"]
                latest = qs[0]
                row["detail"]["op_income_latest"] = latest.get("operating_income")
                ni, ocf_q = latest.get("net_income"), latest.get("operating_cash_flow")
                row["detail"]["ni_pos"] = None if ni is None else bool(ni > 0)
                row["detail"]["ocf_ok"] = None if ni is None or ocf_q is None else bool(ocf_q >= 0.8 * ni)
                # （探索）近四季版關卡：用四季合計比，避開單季營運資金波動
                last4 = qs[:4]
                if len(last4) == 4 and all(q.get("net_income") is not None and q.get("operating_cash_flow") is not None for q in last4):
                    tni, tocf = sum(q["net_income"] for q in last4), sum(q["operating_cash_flow"] for q in last4)
                    row["detail"]["ttm_gate"] = bool(tni > 0 and tocf >= 0.8 * tni)
                else:
                    row["detail"]["ttm_gate"] = None
                row["detail"]["event"] = forward_event(periods, latest["fiscal_period_end"])
                row["detail"]["event1"] = forward_event(periods, latest["fiscal_period_end"], 1)
            dd, ex = price_outcomes(R, tk, d)
            row["dd"], row["excess"] = dd, ex
            res[(tk, str(d))] = row
    out = cache / f"screen_{ocf}{tag}.pkl"
    out.write_bytes(pickle.dumps(res))
    st = collections.Counter(v["tiers"][HEADLINE_TIER] for v in res.values())
    E.log(f"[screen] 完成 {len(res)} 筆（{time.time()-t0:.0f}s）；{HEADLINE_TIER}等級狀態分佈：{dict(st)}")


# ═════════════════════════════════════════════════════════════════════════════
# analyze：通過 vs 被淘汰，之後誰比較好？
# ═════════════════════════════════════════════════════════════════════════════

def cluster_diff(df: pd.DataFrame, flag: str, outcome: str, seed: int = E.RNG_SEED):
    """
    flag=True 的組（被淘汰）與 False 的組（通過）之間，outcome 平均的差（淘汰−通過）。
    信賴區間以股票為單位重抽（同一檔股票在不同查詢日互相不獨立）。
    回傳 (淘汰組平均, 通過組平均, 差, (下限, 上限), 淘汰組筆數, 通過組筆數)。
    """
    g = df.groupby("ticker")
    n1 = g.apply(lambda x: (x[flag]).sum()).values.astype(float)
    n0 = g.apply(lambda x: (~x[flag]).sum()).values.astype(float)
    s1 = g.apply(lambda x: x.loc[x[flag], outcome].sum()).values.astype(float)
    s0 = g.apply(lambda x: x.loc[~x[flag], outcome].sum()).values.astype(float)
    if n1.sum() == 0 or n0.sum() == 0:
        return None
    r1, r0 = s1.sum() / n1.sum(), s0.sum() / n0.sum()
    idx = np.random.default_rng(seed).integers(0, len(n1), size=(E.BOOT_N, len(n1)))
    with np.errstate(invalid="ignore", divide="ignore"):
        boots = s1[idx].sum(1) / n1[idx].sum(1) - s0[idx].sum(1) / n0[idx].sum(1)
    boots = boots[np.isfinite(boots)]
    return r1, r0, r1 - r0, (np.percentile(boots, 2.5), np.percentile(boots, 97.5)), int(n1.sum()), int(n0.sum())


def fmt_split(label: str, res) -> str:
    if res is None:
        return f"{label:<26} (無法比較)"
    r1, r0, d, ci, n1, n0 = res
    return (f"{label:<26} 淘汰{n1:>5}筆: {r1*100:5.1f}% | 通過{n0:>5}筆: {r0*100:5.1f}% | "
            f"差(淘汰−通過) {d*100:+5.1f}個百分點 [{ci[0]*100:+5.1f}, {ci[1]*100:+5.1f}]")


def build_table(cache: Path, ocf: str, tag: str = ""):
    res = pickle.loads((cache / f"screen_{ocf}{tag}.pkl").read_bytes())
    st = E.Study(cache, "clean_gemini")
    rows = []
    for d in E.QUERY_DATES:
        tks = set()
        for node in st.win[d]:
            m = st.meta.get(node)
            if m and E.usable(node, st.meta, True) and m["market"] == "TW":
                tks.add(m["ticker"])
        for tk in tks:
            r = res.get((tk, str(d)))
            if r is None:
                continue
            det = r["detail"]
            fmax = det.get("f_score_max") or 0
            rows.append({
                "ticker": tk, "date": d, "status": r["tiers"],
                "ratio": (det["f_score"] / fmax) if fmax else np.nan,
                "gate": det.get("hard_gate_pass"), "oi": det.get("op_income_latest"),
                "ni_pos": det.get("ni_pos"), "ocf_ok": det.get("ocf_ok"), "ttm_gate": det.get("ttm_gate"),
                "event": r.get("detail", {}).get("event"), "event1": r.get("detail", {}).get("event1"),
                "dd": r["dd"], "excess": r["excess"],
            })
    df = pd.DataFrame(rows)
    df["avail"] = df.status.map(lambda s: s[TIERS[0]] != "unavailable")
    return df, st, res


def basket_effect(st, res, tier: str, lo: dt.date, hi: dt.date, event_key: str = "event"):
    """
    實際籃子內的「篩選前 vs 篩選後」：每個查詢取籃子裡能評估的台股，篩選前=全部的惡化率，
    篩選後=只算被留下的；只納入同時有被留下與被淘汰標的的查詢。差距=篩選前−篩選後（正=篩選有幫助）。
    """
    seed_tk = {s_["name"]: s_["ticker"] for s_ in st.seeds}
    out = []
    for key, case in st.baskets.items():
        d = dt.date.fromisoformat(case["date"])
        if not (lo <= d <= hi):
            continue
        picks, seen = [], set()
        for b in case["basket"]:
            m = st.meta.get(b["node"])
            if not m or not E.usable(b["node"], st.meta, True) or m["market"] != "TW":
                continue
            tk = m["ticker"]
            r_ = res.get((tk, case["date"]))
            if tk in seen or tk == seed_tk[case["seed"]] or r_ is None or r_["tiers"][tier] == "unavailable":
                continue
            ev = r_["detail"].get(event_key)
            if ev is None:
                continue
            seen.add(tk)
            picks.append((r_["tiers"][tier] == "rejected", float(ev)))
        m_, rn = len(picks), sum(x[0] for x in picks)
        if m_ < 2 or rn == 0 or rn == m_:
            continue
        evs = np.array([x[1] for x in picks]); rej = np.array([x[0] for x in picks])
        out.append((seed_tk[case["seed"]], evs.mean(), evs[~rej].mean()))
    if not out:
        return None
    o = pd.DataFrame(out, columns=["seed", "before", "after"])
    o["gain"] = o.before - o.after
    m, ci = E.cluster_boot_mean(o.gain, o.seed)
    return {"n": len(o), "before": o.before.mean(), "after": o.after.mean(), "gain": m, "ci": ci,
            "better": int((o.gain > 1e-9).sum()), "same": int((o.gain.abs() <= 1e-9).sum()), "worse": int((o.gain < -1e-9).sum())}


def fmt_basket(r) -> str:
    if r is None:
        return "(樣本不足)"
    return (f"{r['n']:>4}個查詢 | 篩選前 {r['before']*100:4.1f}% → 篩選後 {r['after']*100:4.1f}% | "
            f"改善 {r['gain']*100:+4.1f}個百分點 [{r['ci'][0]*100:+4.1f}, {r['ci'][1]*100:+4.1f}] | "
            f"變好{r['better']}/不變{r['same']}/變差{r['worse']}")


def stage_analyze(cache: Path, out_dir: Path, ocf: str, tag: str = ""):
    df, st, res = build_table(cache, ocf, tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []

    def P(s=""):
        E.log(s)
        lines.append(s)

    P(f"# 財務篩選驗證（營業現金流版本={ocf}；台股；只用 as-of 當天已公告的財報）")
    P(f"圖譜候選池內的台股（查詢日×股票）{len(df)} 筆，其中財報可評估 {int(df.avail.sum())} 筆（{df.avail.mean()*100:.0f}%）")
    d_ok = df[df.avail & df.event.notna() & (df.date <= LAST_DATE_FUNDAMENTAL)].copy()
    d_ok["event"] = d_ok.event.astype(float)
    p_ok = df[df.avail & df.dd.notna() & (df.date <= LAST_DATE_PRICE)].copy()
    p_ok["big_dd"] = (p_ok.dd <= DD_EVENT).astype(float)
    P(f"主要成績可用：{len(d_ok)} 筆（查詢日 ≤ {LAST_DATE_FUNDAMENTAL}，{d_ok.date.nunique()} 個查詢日，{d_ok.ticker.nunique()} 檔）；"
      f"惡化事件基準率 {d_ok.event.mean()*100:.1f}%")
    P(f"次要成績可用：{len(p_ok)} 筆（查詢日 ≤ {LAST_DATE_PRICE}，{p_ok.date.nunique()} 個查詢日）；"
      f"6 個月內回撤達 {DD_EVENT*100:.0f}% 的基準率 {p_ok.big_dd.mean()*100:.1f}%")

    P("\n## 1. 三個風險等級：通過率與之後的表現")
    for tier in TIERS:
        for name, sub, col in (("主要·之後兩季惡化事件", d_ok, "event"), ("次要·6個月回撤達-30%", p_ok, "big_dd")):
            sub = sub.copy()
            sub["rej"] = sub.status.map(lambda s: s[tier] == "rejected")
            P(f"{tier} 通過率 {(~sub.rej).mean()*100:4.0f}% | " + fmt_split(name, cluster_diff(sub, "rej", col)))
    P(f"（均衡等級之下，淘汰組佔全部惡化事件的比例：" +
      f"{d_ok[d_ok.status.map(lambda s: s[HEADLINE_TIER]=='rejected')].event.sum() / max(d_ok.event.sum(),1)*100:.0f}%，"
      f"而淘汰的筆數佔 {d_ok.status.map(lambda s: s[HEADLINE_TIER]=='rejected').mean()*100:.0f}%——若兩者差不多，等於沒有鑑別力）")

    P(f"\n## 2. 跟簡單規則比（{HEADLINE_TIER}等級為主；主要成績）")
    base = d_ok.copy()
    rules = {
        f"完整篩選（{HEADLINE_TIER}）": base.status.map(lambda s: s[HEADLINE_TIER] == "rejected"),
        "只用硬性關卡（現行定義）": ~base.gate.astype(bool),
        "只用F-score≥50%（不含關卡）": base.ratio < 0.5,
        "只看最新季營業利益為正": ~(base.oi > 0),
        "（舊關卡的一半）淨利為正": ~base.ni_pos.fillna(False).astype(bool),
        "（舊關卡的另一半）單季現金流≥0.8淨利": ~base.ocf_ok.fillna(False).astype(bool),
        "（探索）舊關卡改近四季版": ~base.ttm_gate.fillna(False).astype(bool),
    }
    for label, rej in rules.items():
        sub = base.assign(rej=rej.values)
        P(f"通過率 {(~sub.rej).mean()*100:4.0f}% | " + fmt_split(label, cluster_diff(sub, "rej", "event")))

    P("\n## 2b. 拿掉硬性關卡、只用 F-score 通過比例（用三個等級的門檻；探索性分析）")
    for name, thr in (("門檻80%（保守）", 0.8), ("門檻50%（均衡）", 0.5), ("門檻25%（積極）", 0.25)):
        sub = base.assign(rej=(base.ratio < thr))
        P(f"通過率 {(~sub.rej).mean()*100:4.0f}% | " + fmt_split(name, cluster_diff(sub, "rej", "event")))

    P("\n## 3. F-score 通過比例越高，之後惡化事件越少嗎？（主要成績）")
    bins = [(0, 0.25), (0.25, 0.5), (0.5, 0.8), (0.8, 1.01)]
    for lo, hi in bins:
        sub = d_ok[(d_ok.ratio >= lo) & (d_ok.ratio < hi)]
        if len(sub):
            P(f"通過比例 [{lo:.2f}, {min(hi,1):.2f}) : {len(sub):>5} 筆，惡化事件 {sub.event.mean()*100:5.1f}%")
    from scipy.stats import spearmanr
    rho = spearmanr(d_ok.ratio, d_ok.event, nan_policy="omit")
    P(f"Spearman(F-score通過比例, 惡化事件) = {rho.correlation:+.3f}（負值=分數越高越少惡化）")

    P("\n## 4. 附加：6 個月超額報酬（相對加權指數）；通過 − 被淘汰")
    pe = p_ok.copy()
    pe["rej"] = pe.status.map(lambda s: s[HEADLINE_TIER] == "rejected")
    r = cluster_diff(pe, "rej", "excess")
    if r:
        P(f"{HEADLINE_TIER}：淘汰組平均 {r[0]*100:+.1f}% | 通過組平均 {r[1]*100:+.1f}% | 通過−淘汰 {-r[2]*100:+.1f}個百分點 "
          f"[{-r[3][1]*100:+.1f}, {-r[3][0]*100:+.1f}]（行情單一、僅供參考）")

    P(f"\n## 5. 實際籃子內：篩選前 vs 篩選後的惡化率（版本B的籃子）")
    for tier in TIERS:
        P(f"{tier}  " + fmt_basket(basket_effect(st, res, tier, dt.date(2025, 1, 1), LAST_DATE_FUNDAMENTAL)))

    P("\n## 6. 被篩選涵蓋的程度（版本B的籃子）")
    tot = tw = scr = 0
    for case in st.baskets.values():
        for b in case["basket"]:
            tot += 1
            m = st.meta.get(b["node"])
            if m and E.usable(b["node"], st.meta, True) and m["market"] == "TW":
                tw += 1
                r_ = res.get((m["ticker"], case["date"]))
                scr += int(r_ is not None and r_["tiers"][HEADLINE_TIER] != "unavailable")
    P(f"籃子裡共 {tot} 個標的，其中台股 {tw} 個（{tw/tot*100:.0f}%），財報能評估、真的被篩選到的 {scr} 個（{scr/tot*100:.0f}%）。"
      f"其餘（美股等）在回溯驗證中視為無法評估、等於沒有被篩選。")


    SPLIT = dt.date(2025, 12, 31)
    P("\n## 7. 前後半段是否一致（前半段=查詢日 2025/12 以前，後半段=2026/1 以後）")
    for tier in TIERS:
        for nm, sub in (("前半段", d_ok[d_ok.date <= SPLIT]), ("後半段", d_ok[d_ok.date > SPLIT])):
            sub = sub.assign(rej=sub.status.map(lambda s_: s_[tier] == "rejected"))
            P(f"{tier} {nm} 候選池 通過率 {(~sub.rej).mean()*100:3.0f}% | " + fmt_split("之後兩季惡化", cluster_diff(sub, "rej", "event")))
        lo, mid, hi = dt.date(2025, 1, 1), SPLIT, LAST_DATE_FUNDAMENTAL
        P(f"{tier} 實際籃子 前半段  " + fmt_basket(basket_effect(st, res, tier, lo, mid)))
        P(f"{tier} 實際籃子 後半段  " + fmt_basket(basket_effect(st, res, tier, mid + dt.timedelta(days=1), hi)))

    P("\n## 8. 小型驗證：查詢日 2026/5/15 與 6/1（之前所有分析都沒用到，因為之後只有一季財報；成績=之後一季惡化事件）")
    h = df[df.avail & df.event1.notna() & (df.date > LAST_DATE_FUNDAMENTAL)].copy()
    h["event1"] = h.event1.astype(float)
    P(f"樣本 {len(h)} 筆（{h.date.nunique()} 個查詢日、{h.ticker.nunique()} 檔）；之後一季惡化事件基準率 {h.event1.mean()*100:.1f}%")
    for tier in TIERS:
        sub = h.assign(rej=h.status.map(lambda s_: s_[tier] == "rejected"))
        P(f"{tier} 候選池 通過率 {(~sub.rej).mean()*100:3.0f}% | " + fmt_split("之後一季惡化", cluster_diff(sub, "rej", "event1")))
        P(f"{tier} 實際籃子  " + fmt_basket(basket_effect(st, res, tier, LAST_DATE_FUNDAMENTAL + dt.timedelta(days=1), dt.date(2026, 6, 30), "event1")))

    (out_dir / f"report_financial_screen_{ocf}{tag}.md").write_text("\n".join(lines), encoding="utf-8")
    E.log(f"\nsaved: {out_dir/f'report_financial_screen_{ocf}{tag}.md'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=["fetch", "screen", "analyze"])
    ap.add_argument("--cache-dir", default=str(E.DEFAULT_CACHE))
    ap.add_argument("--out-dir", default=str(E.DEFAULT_OUT))
    ap.add_argument("--ocf", default="fixed", choices=["fixed", "asis"])
    ap.add_argument("--tag", default="", help="輸出檔名後綴，例如 _netincome_gate，避免蓋掉舊關卡的結果")
    a = ap.parse_args()
    cache = Path(a.cache_dir)
    if a.stage == "fetch":
        stage_fetch(cache)
    elif a.stage == "screen":
        stage_screen(cache, a.ocf, a.tag)
    else:
        stage_analyze(cache, Path(a.out_dir), a.ocf, a.tag)
