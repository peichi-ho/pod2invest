"""
驗證圖譜：圖譜挑出的標的，跟查詢標的的股價是否「比隨機抽的更同步」。

【驗證問題】
  使用者查「A 的供應鏈上游 / 下游 / 競爭對手 / 共同主題」，圖譜給的 basket（實驗組），
  是否比「同一時期圖譜裡沒被挑中的其他標的」（對照組）更貼近 A？
  「貼近」用股價衡量：每日漲跌幅（不是股價本身）跟 A 的 Pearson 相關係數。

【事先固定的規則（看結果之前就定好，避免事後挑選）】
  - 查詢日：2025 年 5~9 月每月 1 日與 15 日，共 10 天；回看窗口 30 天
  - 查詢標的：被最多集數提到的前 30 個台股/美股公司（排除 ETF、非公司、其他市場）
  - 測量有效性：圖譜裡有些概念名詞（HBM、PCB、CCL…）被誤判成冷門股票代號，它們的股價跟
    查詢標的無關，放進任何一組都只是雜訊。主要結果（variant=clean）把人工確認過的
    NOT_A_STOCK_NODES 從兩組一律排除；variant=asis 完全照產品現狀，兩者並列
  - 投資目的：4 種全跑
  - 實驗組：呼叫專案內既有的候選產生 + select_basket。為了不對 Gemini 打上萬次，
    LLM 方向/替代判斷與財務篩選在這裡關閉（LLM 判斷失敗時，正式程式碼本來就是
    「保守放行」，所以關閉等於正式環境最寬鬆的情況；實驗組只會比正式產品略差，
    對圖譜是保守的）
  - 對照組：同日期窗口內出現在圖譜裡、但沒被實驗組選中的標的；同數量、同市場
    （實驗組有幾檔台股就抽幾檔台股、有幾檔美股就抽幾檔美股），抽 1000 次
  - 相關性：查詢日之後 30 個交易日的每日漲跌幅。跨台美市場的配對有做時差對齊
    （台股當日 vs 美股前一個交易日），見 build_returns()
  - 輸贏：實驗組平均相關性高於對照組 95% 的抽樣結果才算「贏」（若圖譜沒有資訊，
    隨機情況下只會有 5% 的案例贏）
  - 主要結果 = 上面這組；其餘（更嚴格的對照組、扣掉大盤、不同驗證期、只看同市場）
    都只是穩健性檢查

用法（專案根目錄）：
    python apps/knowledge_graph/scripts/eval_graph_vs_random.py --stage meta
    python apps/knowledge_graph/scripts/eval_graph_vs_random.py --stage prices
    python apps/knowledge_graph/scripts/eval_graph_vs_random.py --stage seeds
    python apps/knowledge_graph/scripts/eval_graph_vs_random.py --stage baskets
    python apps/knowledge_graph/scripts/eval_graph_vs_random.py --stage analyze
每個階段的結果都會存到 --cache-dir，可以中斷後續跑。
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import functools
import json
import math
import os
import re
import sys
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

import numpy as np
import pandas as pd
from django.db import connections

# ── 實驗參數（事先固定）─────────────────────────────────────────────────────
DATA_LAST_DAY = dt.date(2026, 6, 13)          # 圖譜資料的最後一天；之後的日期沒有 30 天窗口可用
OLD_PERIOD_LAST_DATE = dt.date(2025, 9, 15)   # 第一輪分析（2025/5~9）的最後一個查詢日
QUERY_DATES = [
    dt.date(y, m, d)
    for y, m in [(2025, mm) for mm in range(5, 13)] + [(2026, mm) for mm in range(1, 7)]
    for d in (1, 15)
    if dt.date(y, m, d) <= DATA_LAST_DAY
]
WINDOW_DAYS = 30
N_SEEDS = 30
PURPOSES = ["supply_beneficiary", "supply_customer", "diversify_exposure", "same_theme"]
VERIFY_DAYS = 30            # 主要驗證期（交易日）；穩健性檢查另外跑 20 / 60
N_DRAWS = 1000
RNG_SEED = 20251013
K_MIN = 3                   # 實驗組可比較的標的數低於這個值，不納入比較
BOOT_N = 2000
PRICE_START, PRICE_END = "2025-02-01", "2026-10-14"

# 人工檢查圖譜裡的原始說明後，確認這些節點指的不是 Yahoo 上同名代號那家公司
# （產品/材料名詞或未上市公司，被 resolve_ticker 的「全大寫≤6碼就當代號」規則誤判），
# 或是機械性高相關（ETF、同一家公司的不同掛牌）。這份清單在看到任何相關性結果之前定案。
# variant=clean 時從實驗組與對照組一律排除；variant=asis 時完全照產品現狀，用來對照。
NOT_A_STOCK_NODES = {
    "ASIC": "晶片類型（概念）", "CCL": "銅箔基板（材料）", "EVG": "EV Group（未上市）",
    "HBM": "高頻寬記憶體（產品）", "NSP": "指涉不明，非 Yahoo 同名公司", "PCB": "印刷電路板（產品）",
    "AURA": "影像生成模型", "MG": "MG 汽車品牌", "MOMO": "台灣 momo 電商（非 Hello Group）",
    "VIVO": "vivo 手機品牌（未上市）", "UG": "飲料店", "SIG": "藍牙規範組織", "AZ": "指涉不明",
    "SOXX": "ETF", "台積電ADR": "與台積電是同一家公司的不同掛牌",
    # 第二輪（資料延長到 2026/6）新增的節點，同樣在看到任何相關性結果之前，逐一對照圖譜原始說明確認
    "CPU": "處理器（概念）", "LPX": "NVIDIA 機櫃產品名", "VERA": "NVIDIA Vera CPU（產品）",
    "DPC": "指 DeepSeek（未上市）", "TOK": "指日本東京應化，非 Yahoo 上的同名 ETF",
    "AES": "指台灣電源供應商，非美國 AES 電力公司", "AS": "指涉不明，非 Amer Sports",
    "CSB": "指涉不明，非 Yahoo 上的同名 ETF", "GT": "指涉不明，非 Goodyear",
    "S": "指光聖等光通訊公司，非 SentinelOne", "ABTC": "說明為關鍵礦產，非 American Bitcoin",
}

INDEX_COLS = ("^TWII", "^GSPC")
MKT_INDEX = {"TW": "^TWII", "US": "^GSPC"}
DEFAULT_CACHE = ROOT / "kg_eval_results" / "_cache"
DEFAULT_OUT = ROOT / "kg_eval_results"


def log(msg: str):
    print(msg, flush=True)


def jload(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def jsave(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def market_of(ticker: str) -> str:
    if ticker.endswith((".TW", ".TWO")):
        return "TW"
    if "." in ticker:       # .KS .T .HK .SS .DE ...：樣本太少，抽不出同市場對照組
        return "OTHER"
    return "US"


def usable(name: str, meta: dict, clean: bool) -> bool:
    m = meta.get(name)
    return bool(m and m["company"]) and not (clean and name in NOT_A_STOCK_NODES)


def load_graph_tables():
    with connections["knowledge_graphdb"].cursor() as c:
        c.execute("SELECT name, industry FROM nodes")
        nodes = {n: (i or "其他") for n, i in c.fetchall()}
        c.execute("SELECT source, target, relation_type, summary_date, podcast_source FROM links")
        links = c.fetchall()
    return nodes, links


# ═════════════════════════════════════════════════════════════════════════════
# 階段 1：節點 → 股票代號 / 市場
# ═════════════════════════════════════════════════════════════════════════════

def stage_meta(cache: Path):
    from apps.summaries.services.backtesting import resolve_ticker
    from apps.knowledge_graph.services.basket_selection import (
        _is_non_company_ticker, _VERIFIED_FALSE_POSITIVE_NODES,
    )

    nodes, _ = load_graph_tables()
    path = cache / "node_meta.json"
    meta = jload(path) or {}
    t0 = time.time()
    for name, ind in nodes.items():
        if name in meta:
            continue
        try:
            tk = resolve_ticker(name)
        except Exception:
            tk = ""
        mk = market_of(tk) if tk else ""
        # company = 這個節點可以當「一檔公司股票」來比較股價。ETF/基金的相關性來自
        # 持股組成（例如 0050 裡台積電佔一半），是機械性相關，不是「找到相關公司」，
        # 對實驗組跟對照組都排除。
        company = (
            bool(tk) and mk in ("TW", "US")
            and not _is_non_company_ticker(tk)
            and name not in _VERIFIED_FALSE_POSITIVE_NODES
            and ind != "ETF/基金"
        )
        meta[name] = {"ticker": tk, "market": mk, "industry": ind, "company": company}
    jsave(path, meta)
    n_co = sum(m["company"] for m in meta.values())
    log(f"[meta] {len(meta)} nodes, {n_co} usable company nodes ({time.time()-t0:.0f}s)")


# ═════════════════════════════════════════════════════════════════════════════
# 階段 2：股價 → 每日漲跌幅（統一成台股交易日時鐘）
# ═════════════════════════════════════════════════════════════════════════════

def _download(tickers: list[str], chunk: int = 40) -> pd.DataFrame:
    import yfinance as yf

    frames = []
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        df = yf.download(
            part, start=PRICE_START, end=PRICE_END, auto_adjust=True,
            progress=False, threads=True, group_by="column",
        )
        if df is None or df.empty:
            continue
        close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].rename(columns={"Close": part[0]})
        frames.append(close)
        log(f"  downloaded {min(i+chunk, len(tickers))}/{len(tickers)}")
    px = pd.concat(frames, axis=1) if frames else pd.DataFrame()
    px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, ~px.columns.duplicated()]
    return px.dropna(axis=1, how="all")


def build_returns(px_tw: pd.DataFrame, px_us: pd.DataFrame) -> pd.DataFrame:
    """
    所有標的都轉成「台股交易日」時鐘上的每日漲跌幅，這樣台股–美股配對才不會被時差拉低：
      台股第 d 日的盤中，反映的是美股「d 之前最後一個交易日」的收盤資訊，
      所以把每個美股交易日 u 的報酬，對到「嚴格晚於 u 的第一個台股交易日」。
      同一個台股交易日對到多個美股交易日（台股休市時）就複利合併。
    台股–台股、美股–美股的配對，兩邊做完全相同的轉換，不影響彼此的相關性。
    漲跌幅用 p_t / p_{t-1} - 1，不補值：停牌日報酬缺值，不會被假裝成 0。
    """
    r_tw = px_tw / px_tw.shift(1) - 1
    r_us = px_us / px_us.shift(1) - 1
    tw_cal = px_tw["^TWII"].dropna().index
    r_tw = r_tw.reindex(tw_cal)

    pos = tw_cal.searchsorted(r_us.index, side="right")
    keep = pos < len(tw_cal)
    mapped = tw_cal[pos[keep]]
    g = (1 + r_us.loc[keep]).groupby(mapped).prod(min_count=1) - 1
    r_us_tw = g.reindex(tw_cal)
    return pd.concat([r_tw, r_us_tw], axis=1)


def stage_prices(cache: Path):
    meta = jload(cache / "node_meta.json")
    tickers = sorted({m["ticker"] for m in meta.values() if m["company"]})
    tw = [t for t in tickers if market_of(t) == "TW"] + ["^TWII"]
    us = [t for t in tickers if market_of(t) == "US"] + ["^GSPC"]
    log(f"[prices] TW {len(tw)}  US {len(us)}")
    px_tw, px_us = _download(tw), _download(us)

    # TickerMap 有些台股的上市(.TW)/上櫃(.TWO)後綴標錯，Yahoo 查不到。缺資料的台股改試另一種
    # 後綴，抓得到就把 node_meta 裡的代號一併更正（這跟績效無關，只是補資料覆蓋率）。
    def swap(t):
        return t[:-3] + ".TWO" if t.endswith(".TW") else t[:-4] + ".TW"

    missing_tw = [t for t in tw if t not in px_tw.columns and t.endswith((".TW", ".TWO"))]
    if missing_tw:
        alt = {t: swap(t) for t in missing_tw}
        px_alt = _download(list(alt.values()))
        fixed = {t: a for t, a in alt.items() if a in px_alt.columns}
        for t, a in fixed.items():
            px_tw[a] = px_alt[a]
        for m in meta.values():
            if m["ticker"] in fixed:
                m["ticker"] = fixed[m["ticker"]]
        jsave(cache / "node_meta.json", meta)
        log(f"[prices] fixed exchange suffix for {len(fixed)} TW tickers: {fixed}")
    missing = sorted(set(tw + us) - set(px_tw.columns) - set(px_us.columns))
    log(f"[prices] got TW {px_tw.shape[1]}  US {px_us.shape[1]}; no data for {len(missing)}: {missing}")
    R = build_returns(px_tw, px_us)
    R.to_pickle(cache / "returns.pkl")
    log(f"[prices] returns {R.shape}, {R.index.min().date()} → {R.index.max().date()}")


def load_returns(cache: Path) -> pd.DataFrame:
    return pd.read_pickle(cache / "returns.pkl")


# ═════════════════════════════════════════════════════════════════════════════
# 階段 3：查詢標的（依規則選，不看結果）
# ═════════════════════════════════════════════════════════════════════════════

def stage_seeds(cache: Path):
    meta = jload(cache / "node_meta.json")
    R = load_returns(cache)
    _, links = load_graph_tables()
    eps = collections.defaultdict(set)
    for s, t, _rt, _sd, ps in links:
        eps[s].add(ps)
        eps[t].add(ps)

    # 同一檔股票可能有多個別名節點（例如「MSFT」「微軟」），合併計算被提到的集數
    by_ticker = collections.defaultdict(lambda: {"eps": set(), "names": []})
    for name, m in meta.items():
        if not usable(name, meta, clean=True) or m["ticker"] not in R.columns:
            continue
        g = by_ticker[m["ticker"]]
        g["eps"] |= eps.get(name, set())
        g["names"].append((len(eps.get(name, ())), name))

    seeds = []
    for tk, g in sorted(by_ticker.items(), key=lambda kv: -len(kv[1]["eps"])):
        if R.loc["2025-04-01":"2025-12-31", tk].notna().sum() < 100:
            continue
        name = max(g["names"])[1]
        seeds.append({
            "name": name, "ticker": tk, "market": market_of(tk),
            "industry": meta[name]["industry"], "episodes": len(g["eps"]),
            "aliases": [n for _, n in sorted(g["names"], reverse=True)],
        })
        if len(seeds) == N_SEEDS:
            break
    jsave(cache / "seeds.json", seeds)
    log(f"[seeds] {len(seeds)} seeds: " + ", ".join(f"{s['name']}({s['market']})" for s in seeds))
    dup = {tk: g["names"] for tk, g in by_ticker.items() if len(g["names"]) > 1}
    log(f"[seeds] tickers with >1 alias node in graph (node-merge gaps): {len(dup)}; e.g. {list(dup.items())[:6]}")


# ═════════════════════════════════════════════════════════════════════════════
# 階段 4：產生實驗組（跑既有的查詢流程）
# ═════════════════════════════════════════════════════════════════════════════

def _install_patches(price_tickers: set[str], meta: dict, clean: bool, gemini: bool = False):
    """
    只改「速度」與「關掉 LLM」，不改任何篩選邏輯（clean 版本另外讓節點類型過濾
    多認得 NOT_A_STOCK_NODES，這樣被剔除的名額會由下一名候選遞補，而不是讓 basket 變小）：
      - 連線查詢加快取（同日期同關係類型的 links 只撈一次）
      - Leiden 結果加快取（演算法 seed 固定，同日期結果本來就一樣）
      - resolve_ticker 用預先算好的對照表
      - _verify_ticker_live 先看有沒有抓到股價，沒有才呼叫原本的檢查
      - 方向/替代判斷與 reason 整合預設不呼叫 Gemini；gemini=True 時保留正式的判斷函式，
        只把底層的 Gemini 呼叫換成「依提示文字快取」的版本（見 LLM / stage_baskets）
      - reason 整合（synthesize_reason）永遠關閉，它不影響選誰
    """
    from apps.knowledge_graph.services import (
        basket_selection as bs, graph_window as gw,
        strategy_coimpact as ci, strategy_substitute as sub, strategy_supply as sup,
    )

    cached_fetch = functools.lru_cache(maxsize=None)(gw.fetch_links_in_window)
    for mod in (gw, ci, sub, sup):
        mod.fetch_links_in_window = cached_fetch

    orig_leiden = ci.run_leiden
    leiden_cache = {}

    def cached_leiden(links, resolution=1.0, min_community_size=3):
        key = (id(links), resolution, min_community_size)
        if key not in leiden_cache:
            leiden_cache[key] = orig_leiden(links, resolution=resolution, min_community_size=min_community_size)
        return leiden_cache[key]

    ci.run_leiden = cached_leiden

    orig_resolve, orig_verify = bs.resolve_ticker, bs._verify_ticker_live
    def resolve(name):
        if clean and name in NOT_A_STOCK_NODES:
            return ""
        return meta[name]["ticker"] if name in meta else orig_resolve(name)

    bs.resolve_ticker = resolve
    bs._verify_ticker_live = lambda tk: tk in price_tickers or orig_verify(tk)
    if gemini:
        def llm_call(prompt, *args, **kwargs):
            if LLM["mode"] == "collect":          # 第一階段：只記下會問什麼，一律回 YES 讓流程跑完
                LLM["collected"][prompt] = LLM["collected"].get(prompt, 0) + 1
                return "YES"
            if prompt in LLM["cache"]:
                return LLM["cache"][prompt]
            LLM["misses"] += 1                    # 沒有快取（呼叫失敗過）：丟例外，正式程式碼會保守放行
            raise RuntimeError("gemini cache miss")

        bs._call_gemini_with_retry = llm_call
    else:
        bs._llm_check_direction = lambda reason, supplier, customer: True
        bs._llm_check_substitution = lambda reason, a, b: True
    bs.synthesize_reason = lambda candidate, seed, window_days: ""
    return cached_fetch


# Gemini 判斷的共用狀態：mode = collect（只收集提示）/ cached（只讀快取）
LLM = {"mode": "off", "cache": {}, "collected": {}, "misses": 0}


def _generate(seeds, out, path=None):
    """依序跑所有（查詢日 × 標的 × 目的），已經在 out 裡的案例跳過；path 不為 None 時每個查詢日存檔。"""
    from apps.knowledge_graph.services.basket_purpose import get_strategy_for_purpose
    from apps.knowledge_graph.services.basket_selection import select_basket
    from apps.knowledge_graph.services.strategy_coimpact import co_impact_candidates
    from apps.knowledge_graph.services.strategy_substitute import substitute_candidates
    from apps.knowledge_graph.services.strategy_supply import supply_candidates

    t_all = time.time()
    for d in QUERY_DATES:
        t0 = time.time()
        for seed in seeds:
            name = seed["name"]
            sup = None
            for purpose in PURPOSES:
                key = f"{d}|{name}|{purpose}"
                if key in out:
                    continue
                strategy = get_strategy_for_purpose(purpose)
                if strategy in ("supply_upstream", "supply_downstream"):
                    if sup is None:
                        sup = supply_candidates(name, d, WINDOW_DAYS)
                    cands = sup["upstream" if strategy == "supply_upstream" else "downstream"]
                elif strategy == "substitute":
                    cands = substitute_candidates(name, d, WINDOW_DAYS)
                else:
                    cands = co_impact_candidates(name, d, WINDOW_DAYS)
                sel = select_basket(
                    cands, strategy=strategy, seed=name, window_days=WINDOW_DAYS,
                    risk_tier=None, as_of_date=d,
                )
                out[key] = {
                    "date": str(d), "seed": name, "purpose": purpose,
                    "candidate_count": len(cands), "funnel": sel["funnel"],
                    "basket": [
                        {"node": c["node"], "score": c["score"], "hops": c["hops"]}
                        for c in sel["basket"]
                    ],
                }
        if path:
            jsave(path, out)
        log(f"[baskets] {d} done in {time.time()-t0:.0f}s (total {time.time()-t_all:.0f}s, {len(out)} cases)")
    return out


def _call_gemini_all(prompts: list[str], workers: int, cache_path: Path) -> list[str]:
    """用多個執行緒把不重複的提示一次打完，結果即時存快取（可中斷續跑）。回傳失敗的提示。"""
    from apps.knowledge_graph.generate import _call_gemini_with_retry, get_client

    get_client()    # 先在主執行緒初始化，避免多執行緒同時建立 client
    failed, done, t0 = [], 0, time.time()

    def work(p):
        try:
            return p, _call_gemini_with_retry(p), None
        except Exception as e:
            return p, None, str(e)[:200]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, p) for p in prompts]
        for f in as_completed(futs):
            p, text, err = f.result()
            if err:
                failed.append(p)
            else:
                LLM["cache"][p] = text
            done += 1
            if done % 100 == 0 or done == len(prompts):
                jsave(cache_path, LLM["cache"])
                log(f"[gemini] {done}/{len(prompts)} done, {len(failed)} failed, {time.time()-t0:.0f}s")
    jsave(cache_path, LLM["cache"])
    return failed


def stage_baskets(cache: Path, variant: str, workers: int = 8, dry_run: bool = False):
    gemini = variant.endswith("_gemini")
    meta = jload(cache / "node_meta.json")
    seeds = jload(cache / "seeds.json")
    R = load_returns(cache)
    _install_patches(set(R.columns), meta, clean=variant.startswith("clean"), gemini=gemini)
    path = cache / f"baskets_{variant}.json"

    if not gemini:
        out = _generate(seeds, jload(path) or {}, path)
    else:
        cache_path = cache / "gemini_cache.json"
        LLM["cache"] = jload(cache_path) or {}
        # 第一階段：跑一遍流程，只記下「會問 Gemini 什麼」（一律當作 YES），不花任何 API
        LLM["mode"], LLM["collected"] = "collect", {}
        _generate(seeds, {}, None)
        prompts = list(LLM["collected"])
        todo = [p for p in prompts if p not in LLM["cache"]]
        chars = sum(len(p) for p in todo)
        log(f"[gemini] 流程總共問 {sum(LLM['collected'].values())} 次，不重複的提示 {len(prompts)} 個，"
            f"已有快取 {len(prompts)-len(todo)} 個，需要實際呼叫 {len(todo)} 個（合計約 {chars} 字）")
        if dry_run:
            return
        # 第二階段：打 Gemini（可中斷續跑）
        failed = _call_gemini_all(todo, workers, cache_path)
        log(f"[gemini] 呼叫完成，失敗 {len(failed)} 個（失敗的在正式程式碼裡會保守放行）")
        # 第三階段：用快取結果重新產生籃子
        LLM["mode"], LLM["misses"] = "cached", 0
        out = _generate(seeds, {}, path)
        yes = sum(1 for p in prompts if p in LLM["cache"] and LLM["cache"][p].strip().upper().startswith("YES"))
        log(f"[gemini] 判斷 YES {yes} / NO {len(prompts)-len(failed)-yes} / 失敗 {len(failed)}；產生籃子時快取缺漏 {LLM['misses']} 次")
    sizes = collections.Counter(len(v["basket"]) for v in out.values())
    log(f"[baskets] basket size distribution: {dict(sorted(sizes.items()))}")


# ═════════════════════════════════════════════════════════════════════════════
# 階段 5：計算相關性、對照組、統計
# ═════════════════════════════════════════════════════════════════════════════

def corr_vec(s: np.ndarray, M: np.ndarray, min_obs: int) -> np.ndarray:
    """s（T,）對 M（T,n）每一欄的 Pearson 相關；兩邊都有值的天數才算，不足 min_obs 回 NaN。"""
    ok = ~np.isnan(M) & ~np.isnan(s)[:, None]
    cnt = ok.sum(0)
    c = np.maximum(cnt, 1)
    ms = np.where(ok, s[:, None], 0.0).sum(0) / c
    mm = np.where(ok, M, 0.0).sum(0) / c
    ds = np.where(ok, s[:, None] - ms, 0.0)
    dm = np.where(ok, M - mm, 0.0)
    vs, vm = (ds ** 2).sum(0), (dm ** 2).sum(0)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = (ds * dm).sum(0) / np.sqrt(vs * vm)
    r[(cnt < min_obs) | (vs <= 0) | (vm <= 0)] = np.nan
    return r


def residualize(W: pd.DataFrame, mcol: dict[str, str]) -> pd.DataFrame:
    """每檔股票的漲跌幅，扣掉所屬市場大盤（台股→加權、美股→S&P500）的線性影響。"""
    out = pd.DataFrame(np.nan, index=W.index, columns=W.columns)
    for col in W.columns:
        if col in INDEX_COLS or col not in mcol:
            continue
        m, y = W[mcol[col]].values, W[col].values
        ok = ~np.isnan(m) & ~np.isnan(y)
        if ok.sum() < 8:
            continue
        X = np.c_[np.ones(ok.sum()), m[ok]]
        beta = np.linalg.lstsq(X, y[ok], rcond=None)[0]
        res = np.full_like(y, np.nan, dtype=float)
        res[ok] = y[ok] - X @ beta
        out[col] = res
    return out


def draw_matched(cands: list[np.ndarray], n_total: int, B: int, rng) -> np.ndarray:
    """
    對每個實驗組標的，從它自己的候選集合裡各抽一檔，同一次抽樣內不重複。
    回傳 (B, k) 的 universe 索引。候選用完（極少見）時退回允許重複。
    """
    U = rng.random((B, n_total))
    used = np.zeros((B, n_total), dtype=bool)
    ar = np.arange(B)
    out = np.empty((B, len(cands)), dtype=np.int64)
    for j in np.argsort([len(c) for c in cands], kind="stable"):
        C = cands[j]
        masked = np.where(used[:, C], np.inf, U[:, C])
        ch = C[masked.argmin(axis=1)]
        used[ar, ch] = True
        out[:, j] = ch
    return out


class Study:
    def __init__(self, cache: Path, variant: str):
        self.clean = variant.startswith("clean")
        self.meta = jload(cache / "node_meta.json")
        self.seeds = jload(cache / "seeds.json")
        self.baskets = jload(cache / f"baskets_{variant}.json")
        self.R = load_returns(cache)
        self.tw_cal = self.R.index
        self.cols = list(self.R.columns)
        self.tidx = {t: i for i, t in enumerate(self.cols)}
        self.mcol = {t: MKT_INDEX[market_of(t)] for t in self.cols
                     if t not in INDEX_COLS and market_of(t) in MKT_INDEX}
        _, links = load_graph_tables()
        self.win = {}   # 查詢日 → {node: 該窗口內提到它的集數 set}
        for d in QUERY_DATES:
            lo = d - dt.timedelta(days=WINDOW_DAYS)
            w = collections.defaultdict(set)
            for s, t, _rt, sd, ps in links:
                if lo <= sd <= d:
                    w[s].add(ps)
                    w[t].add(ps)
            self.win[d] = w
        self._wcache = {}

    def window(self, d: dt.date, n: int):
        key = (d, n)
        if key not in self._wcache:
            dates = self.tw_cal[self.tw_cal > pd.Timestamp(d)][:n]
            W = self.R.loc[dates]
            self._wcache[key] = (W, residualize(W, self.mcol))
        return self._wcache[key]

    def pool(self, d: dt.date, seed_ticker: str):
        """同窗口內出現在圖譜裡的公司（依股票代號去重；別名節點的集數合併）。"""
        g = {}
        for node, eps in self.win[d].items():
            m = self.meta.get(node)
            if not usable(node, self.meta, self.clean) or m["ticker"] not in self.tidx or m["ticker"] == seed_ticker:
                continue
            e = g.setdefault(m["ticker"], {"eps": set(), "industry": m["industry"], "best": 0})
            e["eps"] |= eps
            if len(eps) > e["best"]:
                e["best"], e["industry"] = len(eps), m["industry"]
        return {t: {"pop": len(v["eps"]), "industry": v["industry"], "market": market_of(t)} for t, v in g.items()}

    def _placebo_seed(self, i: int, k: int = 1) -> dict:
        """安慰劑：清單裡往後數第 k 個「同市場、不同股票」的查詢標的（循環）。"""
        me, seen = self.seeds[i], 0
        for step in range(1, len(self.seeds)):
            cand = self.seeds[(i + step) % len(self.seeds)]
            if cand["market"] == me["market"] and cand["ticker"] != me["ticker"]:
                seen += 1
                if seen == k:
                    return cand
        return me

    def evaluate(self, n: int = VERIFY_DAYS, same_market_only: bool = False, k_min: int = K_MIN,
                 placebo: int = 0):
        """
        placebo=k（k>=1）：實驗組仍是原查詢標的的 basket，但相關性改拿「另一個同市場的查詢標的」
        來算（對照組也一樣）。若圖譜真的抓到「跟這個標的有關」，優勢應該消失；若優勢還在，
        代表圖譜只是挑到本來就跟什麼都同步的大型熱門股，主要結果就不能照字面解讀。
        """
        min_obs = int(round(0.6 * n))
        cases, skipped = [], collections.Counter()
        drop = collections.Counter()
        for d in QUERY_DATES:
            W, Wres = self.window(d, n)
            Mraw, Mres = W.values, Wres.values
            for si, own_seed in enumerate(self.seeds):
                seed = self._placebo_seed(si, placebo) if placebo else own_seed
                st = seed["ticker"]
                sraw, sres = W[st].values, Wres[st].values
                if np.isnan(sraw).sum() > n - min_obs:
                    skipped["seed_no_price"] += 1
                    continue
                cr, ca = corr_vec(sraw, Mraw, min_obs), corr_vec(sres, Mres, min_obs)
                pool = self.pool(d, st)
                for purpose in PURPOSES:
                    case = self.baskets.get(f"{d}|{own_seed['name']}|{purpose}")
                    if case is None or not case["basket"]:
                        skipped[f"{purpose}:empty_basket"] += 1
                        continue
                    res = self._one_case(case, seed, d, purpose, pool, cr, ca, n, same_market_only, k_min, drop)
                    if isinstance(res, str):
                        skipped[f"{purpose}:{res}"] += 1
                    else:
                        res["own_seed"] = own_seed["name"]
                        cases.append(res)
        return cases, skipped, drop

    def _one_case(self, case, seed, d, purpose, pool, cr, ca, n, same_market_only, k_min, drop):
        st, smk = seed["ticker"], seed["market"]
        ok = lambda t: t in self.tidx and np.isfinite(cr[self.tidx[t]]) and np.isfinite(ca[self.tidx[t]])

        exp, seen = [], set()
        for b in case["basket"]:
            m = self.meta.get(b["node"])
            if not usable(b["node"], self.meta, self.clean):
                drop["not_tw_us_company"] += 1
                continue
            t = m["ticker"]
            if t == st:
                drop["same_ticker_as_seed(alias node)"] += 1
                continue
            if t in seen:
                drop["duplicate_ticker"] += 1
                continue
            if not ok(t):
                drop["no_price_in_window"] += 1
                continue
            if same_market_only and market_of(t) != smk:
                drop["cross_market(excluded by variant)"] += 1
                continue
            seen.add(t)
            exp.append(t)
        if len(exp) < k_min:
            return "too_few_comparable"

        pool = {t: v for t, v in pool.items() if ok(t) and (not same_market_only or v["market"] == smk)}
        for t in exp:   # 實驗組本來就在窗口圖譜內；防呆：萬一不在 pool（別名去重造成）就補進來算熱門度
            pool.setdefault(t, {"pop": 0, "industry": "", "market": market_of(t)})
        exp_set = set(exp)
        universe = sorted(t for t in pool if t not in exp_set)
        u_idx = np.array([self.tidx[t] for t in universe], dtype=np.int64)
        u_mk = np.array([pool[t]["market"] for t in universe])
        u_ind = np.array([pool[t]["industry"] for t in universe])
        k_by_mk = collections.Counter(market_of(t) for t in exp)
        for mk, k in k_by_mk.items():
            if (u_mk == mk).sum() < k:
                return "pool_too_small"

        exp_raw = np.array([cr[self.tidx[t]] for t in exp])
        exp_adj = np.array([ca[self.tidx[t]] for t in exp])
        vals_raw, vals_adj = cr[u_idx], ca[u_idx]

        # 熱門度排序（每個市場內）：用來做「熱門程度相近」的對照組
        pop_pos = {}
        for mk in k_by_mk:
            members = sorted((t for t in pool if pool[t]["market"] == mk), key=lambda t: (-pool[t]["pop"], t))
            pop_pos[mk] = {t: i for i, t in enumerate(members)}

        cand = {"pool": [], "pop": [], "ind": []}
        for t in exp:
            mk = market_of(t)
            same_mk = np.where(u_mk == mk)[0]
            cand["pool"].append(same_mk)
            cand["ind"].append(same_mk[u_ind[same_mk] == seed["industry"]])
            q, size = pop_pos[mk][t], len(pop_pos[mk])
            w = max(3, math.ceil(0.1 * size))
            upos = np.array([pop_pos[mk][universe[i]] for i in same_mk])
            near = same_mk[np.abs(upos - q) <= w]
            while len(near) < 1 and w < size:
                w *= 2
                near = same_mk[np.abs(upos - q) <= w]
            cand["pop"].append(near)

        rng = np.random.default_rng(zlib.crc32(f"{d}|{seed['name']}|{purpose}".encode()) ^ RNG_SEED)
        out = {
            "date": str(d), "seed": seed["name"], "seed_market": smk, "seed_ticker": st, "purpose": purpose,
            "k": len(exp), "picks": ",".join(exp), "n_pool": len(universe),
            "cross_market_picks": sum(market_of(t) != smk for t in exp),
            "exp_pop": float(np.mean([pool[t]["pop"] for t in exp])),
            "pool_pop": float(np.mean([pool[t]["pop"] for t in universe])),
            "res": {}, "draws": {},
        }
        for ctype in ("pool", "pop", "ind"):
            cs = cand[ctype]
            if ctype == "ind" and any(
                len(c) < k_by_mk[market_of(exp[j])] for j, c in enumerate(cs)
            ):
                continue    # 同產業的候選不夠抽，這個案例不納入「同產業」對照
            if ctype != "ind" and any(len(c) < 1 for c in cs):
                continue
            ch = draw_matched(cs, len(universe), N_DRAWS, rng)
            for ckind, ev, vv in (("raw", exp_raw, vals_raw), ("adj", exp_adj, vals_adj)):
                dr = vv[ch].mean(axis=1)
                e = float(ev.mean())
                pct = float((dr < e).mean() + 0.5 * (dr == e).mean())
                out["res"][(ctype, ckind)] = (e, float(dr.mean()), pct)
                out["draws"][(ctype, ckind)] = dr
        return out


def summarize(cases, ctype="pool", ckind="raw", boot_rng=None):
    rows = [c for c in cases if (ctype, ckind) in c["res"]]
    if not rows:
        return None
    exp = np.array([c["res"][(ctype, ckind)][0] for c in rows])
    ctl = np.array([c["res"][(ctype, ckind)][1] for c in rows])
    pct = np.array([c["res"][(ctype, ckind)][2] for c in rows])
    diff = exp - ctl
    seeds = np.array([c["seed_ticker"] for c in rows])
    useeds = np.unique(seeds)
    num = np.array([diff[seeds == s].sum() for s in useeds])
    den = np.array([(seeds == s).sum() for s in useeds])
    rng = boot_rng or np.random.default_rng(RNG_SEED)
    idx = rng.integers(0, len(useeds), size=(BOOT_N, len(useeds)))
    boots = num[idx].sum(1) / den[idx].sum(1)
    agg = np.mean([c["draws"][(ctype, ckind)] for c in rows], axis=0)    # 1000 個「隨機組的整體平均」
    return {
        "n": len(rows), "exp": exp.mean(), "ctrl": ctl.mean(), "diff": diff.mean(),
        "ci": (np.percentile(boots, 2.5), np.percentile(boots, 97.5)),
        "win95": (pct >= 0.95).mean(), "above_median": (pct > 0.5).mean(), "mean_pct": pct.mean(),
        "rand_beat_graph": int((agg >= exp.mean()).sum()),
        "agg_lo_hi": (np.percentile(agg, 2.5), np.percentile(agg, 97.5)),
        "pos_seed_share": float(np.mean([(diff[seeds == s].mean() > 0) for s in useeds])),
        "pos_case_share": float((diff > 0).mean()),
    }


def did_summary(true_cases, placebo_runs, purpose=None, min_runs=3):
    """
    「專屬於查詢標的」的優勢 = 真實標的的優勢（圖譜 − 隨機）− 換成其他標的後的優勢。
    每個案例拿 placebo_runs 裡有效的那幾次取平均；有效次數不足 min_runs 的案例不納入。
    信賴區間以查詢標的為單位重抽（同一個標的的案例互相不獨立）。
    """
    key = lambda c: (c["date"], c["own_seed"], c["purpose"])
    plc = collections.defaultdict(list)
    for run in placebo_runs:
        for c in run:
            e, m, _ = c["res"][("pool", "raw")]
            plc[key(c)].append(e - m)
    seeds, true_d, plc_d = [], [], []
    for c in true_cases:
        if purpose and c["purpose"] != purpose:
            continue
        v = plc.get(key(c), [])
        if len(v) < min_runs or ("pool", "raw") not in c["res"]:
            continue
        e, m, _ = c["res"][("pool", "raw")]
        seeds.append(c["seed_ticker"]); true_d.append(e - m); plc_d.append(float(np.mean(v)))
    if not seeds:
        return None
    seeds, true_d, plc_d = np.array(seeds), np.array(true_d), np.array(plc_d)
    diff = true_d - plc_d
    useeds = np.unique(seeds)
    num = np.array([diff[seeds == s].sum() for s in useeds])
    den = np.array([(seeds == s).sum() for s in useeds])
    idx = np.random.default_rng(RNG_SEED).integers(0, len(useeds), size=(BOOT_N, len(useeds)))
    boots = num[idx].sum(1) / den[idx].sum(1)
    return {
        "n": len(diff), "true": true_d.mean(), "placebo": plc_d.mean(), "specific": diff.mean(),
        "ci": (np.percentile(boots, 2.5), np.percentile(boots, 97.5)),
        "share": diff.mean() / true_d.mean() if true_d.mean() else float("nan"),
    }


def fmt_did(s):
    if s is None:
        return "(無案例)"
    return (f"案例{s['n']:>4} | 真實標的的優勢 {s['true']:+.3f} | 換成別的標的後 {s['placebo']:+.3f} | "
            f"專屬於查詢標的的部分 {s['specific']:+.3f} [{s['ci'][0]:+.3f}, {s['ci'][1]:+.3f}]（約佔 {s['share']*100:.0f}%）")


def fmt(s):
    if s is None:
        return "(無案例)"
    return (f"案例{s['n']:>4} | 圖譜 {s['exp']:.3f} vs 隨機 {s['ctrl']:.3f} | 差 {s['diff']:+.3f} "
            f"[{s['ci'][0]:+.3f}, {s['ci'][1]:+.3f}] | 贏過95%隨機 {s['win95']*100:4.1f}% "
            f"| 高於隨機中位數 {s['above_median']*100:4.1f}% | 隨機1000次中不輸圖譜 {s['rand_beat_graph']} 次")


def stage_analyze(cache: Path, out_dir: Path, k_min: int, variant: str):
    st = Study(cache, variant)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []

    def P(s=""):
        log(s)
        lines.append(s)

    cases, skipped, drop = st.evaluate(VERIFY_DAYS, False, k_min)
    total_pairs = len(QUERY_DATES) * len(st.seeds) * len(PURPOSES)
    P(f"# 圖譜 vs 隨機：結果（版本={variant}，驗證期 {VERIFY_DAYS} 個交易日，K_MIN={k_min}）")
    P(f"查詢組合總數 {total_pairs}（{len(QUERY_DATES)} 日 × {len(st.seeds)} 標的 × {len(PURPOSES)} 目的）；可比較案例 {len(cases)}")
    P("未納入原因：" + "；".join(f"{k}={v}" for k, v in sorted(skipped.items())))
    P("實驗組內被剔除的標的：" + "；".join(f"{k}={v}" for k, v in drop.most_common()))
    ks = [c["k"] for c in cases]
    P(f"可比較案例的實驗組大小：平均 {np.mean(ks):.1f}，中位數 {np.median(ks):.0f}；"
      f"含跨市場標的的案例 {np.mean([c['cross_market_picks']>0 for c in cases])*100:.0f}%")
    P(f"熱門度（被多少集提到）：實驗組平均 {np.mean([c['exp_pop'] for c in cases]):.1f} 集 vs 池內平均 {np.mean([c['pool_pop'] for c in cases]):.1f} 集")

    P("\n## 主要結果（對照組=同期圖譜內沒被選中的標的，同市場同數量；原始相關係數）")
    main = summarize(cases, "pool", "raw")
    P(fmt(main))
    P(f"隨機組整體平均的 95% 範圍：[{main['agg_lo_hi'][0]:.3f}, {main['agg_lo_hi'][1]:.3f}]；"
      f"圖譜為正差距的標的 {main['pos_seed_share']*100:.0f}%（以30個查詢標的計）")

    old = [c for c in cases if dt.date.fromisoformat(c["date"]) <= OLD_PERIOD_LAST_DATE]
    new = [c for c in cases if dt.date.fromisoformat(c["date"]) > OLD_PERIOD_LAST_DATE]

    P("\n## 新舊期間對照（規則與查詢標的完全相同；新期間是第一輪分析時沒看過的資料）")
    for label, sub in (("舊期間 2025/5~9 ", old), ("新期間 2025/10~2026/6", new), ("合併", cases)):
        P(f"{label:<20} " + fmt(summarize(sub, "pool", "raw")))
        for p in PURPOSES:
            P(f"    {p:<20} " + fmt(summarize([c for c in sub if c['purpose'] == p], "pool", "raw")))

    P("\n## 依投資目的（合併）")
    for p in PURPOSES:
        P(f"{p:<20} " + fmt(summarize([c for c in cases if c['purpose'] == p], "pool", "raw")))
    P("\n## 依月份（每月 1 日、15 日合併）")
    for ym in sorted({c["date"][:7] for c in cases}):
        P(f"{ym}  " + fmt(summarize([c for c in cases if c['date'][:7] == ym], "pool", "raw")))
    P("\n## 依查詢標的市場")
    for mk in ("TW", "US"):
        P(f"{mk:<4} " + fmt(summarize([c for c in cases if c['seed_market'] == mk], "pool", "raw")))

    P("\n## 穩健性檢查（越往下越嚴格）")
    for ctype, label in (("pool", "隨機（圖譜內未被選中）"), ("pop", "熱門程度相近的隨機"), ("ind", "同產業的隨機")):
        for ckind, klabel in (("raw", "原始相關"), ("adj", "扣掉大盤後相關")):
            P(f"{label:<14}｜{klabel:<8} " + fmt(summarize(cases, ctype, ckind)))
    for n in (20, 60):
        c2, _, _ = st.evaluate(n, False, k_min)
        P(f"驗證期 {n} 個交易日｜隨機/原始相關  " + fmt(summarize(c2, "pool", "raw")))
    c3, _, _ = st.evaluate(VERIFY_DAYS, True, k_min)
    P("只看與查詢標的同市場的標的 " + fmt(summarize(c3, "pool", "raw")))
    c4, _, _ = st.evaluate(VERIFY_DAYS, False, 1)
    P("放寬到 K_MIN=1（含只有1~2檔的案例） " + fmt(summarize(c4, "pool", "raw")))

    P("\n## 安慰劑檢查：把查詢標的換成同市場的其他標的（5 個取平均），圖譜的優勢還剩多少？")
    placebo_runs = [st.evaluate(VERIFY_DAYS, False, k_min, placebo=k)[0] for k in range(1, 6)]
    P(f"換成別的標的後，圖譜 vs 隨機（第1個替身） " + fmt(summarize(placebo_runs[0], "pool", "raw")))
    P("全部  " + fmt_did(did_summary(cases, placebo_runs)))
    for p in PURPOSES:
        P(f"{p:<20} " + fmt_did(did_summary(cases, placebo_runs, purpose=p)))

    P("\n## 嚴格檢查分新舊期間")
    for label, sub in (("舊期間", old), ("新期間", new)):
        P(f"{label} 熱門程度相近的隨機 " + fmt(summarize(sub, "pop", "raw")))
        P(f"{label} 同產業的隨機       " + fmt(summarize(sub, "ind", "raw")))
        P(f"{label} 扣掉大盤後相關     " + fmt(summarize(sub, "pool", "adj")))
        P(f"{label} 專屬於查詢標的的部分 " + fmt_did(did_summary(sub, placebo_runs)))

    rows = []
    for c in cases:
        r = {k: v for k, v in c.items() if k not in ("res", "draws")}
        for (ctype, ckind), (e, m, p) in c["res"].items():
            r[f"{ctype}_{ckind}_exp"], r[f"{ctype}_{ckind}_ctrl"], r[f"{ctype}_{ckind}_pct"] = e, m, p
        rows.append(r)
    pd.DataFrame(rows).to_csv(out_dir / f"cases_{variant}.csv", index=False, encoding="utf-8-sig")
    (out_dir / f"report_{variant}.md").write_text("\n".join(lines), encoding="utf-8")
    log(f"\nsaved: {out_dir/f'cases_{variant}.csv'}, {out_dir/f'report_{variant}.md'}")


def cluster_boot_mean(values, clusters, seed=RNG_SEED):
    """平均值的 95% 範圍；以 cluster（查詢標的）為單位重抽，因為同一檔標的的案例互相不獨立。"""
    values, clusters = np.asarray(values, float), np.asarray(clusters)
    u = np.unique(clusters)
    num = np.array([values[clusters == c].sum() for c in u])
    den = np.array([(clusters == c).sum() for c in u])
    idx = np.random.default_rng(seed).integers(0, len(u), size=(BOOT_N, len(u)))
    boots = num[idx].sum(1) / den[idx].sum(1)
    return values.mean(), (np.percentile(boots, 2.5), np.percentile(boots, 97.5))


def stage_compare(cache: Path, out_dir: Path, k_min: int):
    """版本 A（沒開 Gemini）對版本 B（開 Gemini 方向/替代判斷）：Gemini 有沒有讓標的更相關？"""
    A, B = Study(cache, "clean"), Study(cache, "clean_gemini")
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []

    def P(s=""):
        log(s)
        lines.append(s)

    P("# Gemini 判斷（版本B）對沒開（版本A）：結果")
    P("\n## 1. Gemini 動了哪些查詢")
    st = collections.defaultdict(collections.Counter)
    removed_nodes = {}
    for key, ca in A.baskets.items():
        cb = B.baskets.get(key)
        if cb is None:
            continue
        a_nodes = [b["node"] for b in ca["basket"]]
        b_nodes = [b["node"] for b in cb["basket"]]
        rem = [n for n in a_nodes if n not in b_nodes]
        add = [n for n in b_nodes if n not in a_nodes]
        c = st[ca["purpose"]]
        c["queries"] += 1
        c["A有結果"] += bool(a_nodes)
        c["B有結果"] += bool(b_nodes)
        c["被改動"] += bool(rem or add)
        c["A標的總數"] += len(a_nodes)
        c["被淘汰"] += len(rem)
        c["遞補"] += len(add)
        if rem:
            removed_nodes[key] = (set(rem), set(n for n in a_nodes if n in b_nodes))
    for p in PURPOSES:
        c = st[p]
        P(f"{p:<20} 查詢{c['queries']:>4} | A有結果{c['A有結果']:>4} B有結果{c['B有結果']:>4} | 被改動的查詢{c['被改動']:>4}"
          f" | A共{c['A標的總數']}檔，被淘汰{c['被淘汰']}檔（{c['被淘汰']/max(c['A標的總數'],1)*100:.1f}%），遞補{c['遞補']}檔")

    cA, _, _ = A.evaluate(VERIFY_DAYS, False, k_min)
    cB, _, _ = B.evaluate(VERIFY_DAYS, False, k_min)
    P("\n## 2. B 本身：圖譜 vs 隨機（跟之前同一套方法）")
    P("全部  " + fmt(summarize(cB, "pool", "raw")))
    for p in PURPOSES:
        P(f"{p:<20} " + fmt(summarize([c for c in cB if c['purpose'] == p], "pool", "raw")))

    P("\n## 3. 同一批查詢，B 的優勢（圖譜−隨機）跟 A 比")
    key = lambda c: (c["date"], c["seed"], c["purpose"])
    ia = {key(c): c for c in cA}
    ib = {key(c): c for c in cB}
    both = sorted(set(ia) & set(ib))
    P(f"A 可比較 {len(ia)}、B 可比較 {len(ib)}、兩邊都可比較 {len(both)}")

    def adv(c):
        e, m, _ = c["res"][("pool", "raw")]
        return e - m

    def paired(keys, label):
        if len(keys) < 5:
            P(f"{label:<28} 案例 {len(keys)}（太少，不報）")
            return
        d = [adv(ib[k]) - adv(ia[k]) for k in keys]
        m, ci = cluster_boot_mean(d, [ia[k]["seed_ticker"] for k in keys])
        a_m = np.mean([adv(ia[k]) for k in keys]); b_m = np.mean([adv(ib[k]) for k in keys])
        P(f"{label:<28} 案例{len(keys):>4} | A的優勢 {a_m:+.3f} → B的優勢 {b_m:+.3f} | 變化 {m:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}]")

    paired(both, "全部（兩邊都可比較）")
    changed = [k for k in both if set(ia[k]["picks"].split(",")) != set(ib[k]["picks"].split(","))]
    P(f"其中被 Gemini 改動了標的的查詢：{len(changed)}")
    paired(changed, "只看被改動的查詢")
    for p in PURPOSES:
        paired([k for k in changed if k[2] == p], f"  被改動 · {p}")

    P("\n## 4. 被 Gemini 淘汰的標的，是不是比留下的更不相關？（同一個查詢內比較，相關係數）")
    rows = []
    for k, (rem, kept) in removed_nodes.items():
        d = dt.date.fromisoformat(k.split("|")[0])
        seed = next(s for s in A.seeds if s["name"] == k.split("|")[1])
        W, _ = A.window(d, VERIFY_DAYS)
        cr = corr_vec(W[seed["ticker"]].values, W.values, int(round(0.6 * VERIFY_DAYS)))

        def vals(nodes):
            out, seen = [], set()
            for n in nodes:
                if not usable(n, A.meta, True):
                    continue
                t = A.meta[n]["ticker"]
                if t == seed["ticker"] or t in seen or t not in A.tidx or not np.isfinite(cr[A.tidx[t]]):
                    continue
                seen.add(t)
                out.append(cr[A.tidx[t]])
            return out

        vr, vk = vals(rem), vals(kept)
        if vr and vk:
            rows.append((k.split("|")[2], seed["ticker"], np.mean(vk) - np.mean(vr), np.mean(vk), np.mean(vr), len(vr), len(vk)))
    if rows:
        df = pd.DataFrame(rows, columns=["purpose", "seed", "gap", "kept", "removed", "n_removed", "n_kept"])
        m, ci = cluster_boot_mean(df.gap, df.seed)
        P(f"全部 {len(df)} 個查詢 | 留下的平均相關 {df.kept.mean():.3f}、被淘汰的 {df.removed.mean():.3f} | "
          f"留下−淘汰 {m:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}]")
        for p in PURPOSES:
            sub = df[df.purpose == p]
            if len(sub) >= 5:
                m, ci = cluster_boot_mean(sub.gap, sub.seed)
                P(f"{p:<20} {len(sub):>3} 個查詢 | 留下 {sub.kept.mean():.3f}、淘汰 {sub.removed.mean():.3f} | 留下−淘汰 {m:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}]")
    else:
        P("（沒有可比較的查詢）")
    (out_dir / "report_compare_A_vs_B.md").write_text("\n".join(lines), encoding="utf-8")
    log(f"\nsaved: {out_dir/'report_compare_A_vs_B.md'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=["meta", "prices", "seeds", "baskets", "analyze", "compare"])
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--k-min", type=int, default=K_MIN)
    ap.add_argument("--variant", default="clean", choices=["clean", "asis", "clean_gemini"],
                    help="clean=排除 NOT_A_STOCK_NODES（版本A）；asis=完全照產品現狀（對照）；clean_gemini=clean＋開啟 Gemini 方向/替代判斷（版本B）")
    ap.add_argument("--workers", type=int, default=8, help="呼叫 Gemini 的並行數")
    ap.add_argument("--dry-run", action="store_true", help="只數出要問 Gemini 幾次，不真的呼叫")
    a = ap.parse_args()
    cache = Path(a.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    {
        "meta": lambda: stage_meta(cache),
        "prices": lambda: stage_prices(cache),
        "seeds": lambda: stage_seeds(cache),
        "baskets": lambda: stage_baskets(cache, a.variant, a.workers, a.dry_run),
        "analyze": lambda: stage_analyze(cache, Path(a.out_dir), a.k_min, a.variant),
        "compare": lambda: stage_compare(cache, Path(a.out_dir), a.k_min),
    }[a.stage]()


if __name__ == "__main__":
    main()
