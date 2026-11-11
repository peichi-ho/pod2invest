# scripts/two_step_fresh_sample.py
"""
兩步做法的外部驗證：另外抽一批「不在 v5 抽查 94 句裡」的驗證句(prompt 和檢查規則的例子大多取自那 94 句，
用同一批驗證會偏樂觀)，跑 Flash v6 拆 → 程式檢查 → Pro 檢查(REVIEW_VERSION)，輸出給人工檢查的清單。
每檔股票最多抽 1 句，避免台積電、輝達佔一大半。只呼叫 Gemini、不寫入資料庫。

用法：python scripts/two_step_fresh_sample.py [--n 40] [--seed 7]　|　--retry-failed(只重跑 Pro 逾時的句子)
      --review-again：沿用 out/two_step_fresh.json 的 Flash 結果，只用目前的檢查版本重做第二步(比較檢查 prompt 用)
輸出：out/two_step_fresh.md(人工檢查用)、out/two_step_fresh.json；--review-again 輸出 two_step_fresh_<版本>.*
"""
import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.summaries.models import ThesisScenarioExtraction
from apps.summaries.services.gemini import make_client
from apps.summaries.services.thesis_scenario import (
    REVIEW_VERSION, build_context, extract_thesis_scenarios, review_scenarios,
)
from compare_thesis_prompt import fmt

OUT = "out"
BASE_VERSION = "v5"      # 比較基準：之前抽查過的 v5 結果


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--extract-model", default="gemini-2.5-flash")
    ap.add_argument("--review-model", default="gemini-3.1-pro-preview")
    ap.add_argument("--retry-failed", action="store_true", help="只重跑上一次 Pro 檢查失敗的那幾句")
    ap.add_argument("--review-again", action="store_true", help=f"沿用 Flash 結果，只用 {REVIEW_VERSION} 重做第二步")
    args = ap.parse_args()
    if args.retry_failed:
        return retry_failed(args.review_model)
    if args.review_again:
        return review_again(args.review_model)

    reviewed = json.load(open(f"{OUT}/type_review_sample.json", encoding="utf-8"))
    used_ids = {s["id"] for s in reviewed}
    used = ThesisScenarioExtraction.objects.using("summariesdb").filter(id__in=used_ids)
    used_theses = {(r.summary_id, r.ticker) for r in used}

    rows = list(ThesisScenarioExtraction.objects.using("summariesdb")
                .filter(prompt_version=BASE_VERSION)
                .exclude(status=ThesisScenarioExtraction.STATUS_FAILED)
                .exclude(id__in=used_ids)
                .only("id", "ticker", "summary_id", "asset_name", "scenarios", "backtest_record_id"))
    rows = [r for r in rows if (r.summary_id, r.ticker) not in used_theses]
    random.Random(args.seed).shuffle(rows)
    picked, tickers = [], set()
    for r in rows:
        if r.ticker not in tickers:
            tickers.add(r.ticker)
            picked.append(r)
        if len(picked) >= args.n:
            break
    print(f"候選 {len(rows)} 筆（排除抽查過的集數與股票），抽 {len(picked)} 句、{len(tickers)} 檔股票", flush=True)

    flash = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
    os.environ["VERTEX_LOCATION"] = "global"      # gemini-3.1-pro-preview 只在 global
    pro = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))

    out = []
    for k, row in enumerate(picked, 1):
        record = row.backtest_record
        context = build_context(record)
        t0 = time.time()
        first = extract_thesis_scenarios(flash, args.extract_model, record, prompt_version="v6")
        rev = review_scenarios(pro, args.review_model, record, first["scenarios"], context, review_version=REVIEW_VERSION)
        out.append({"id": row.id, "ticker": row.ticker, "asset": row.asset_name, "direction": record.direction,
                    "thesis": record.thesis, "evidence_quote": record.evidence_quote, "v5": row.scenarios,
                    "first_status": first["status"], "first": first["scenarios"], "unmapped": first.get("unmapped") or [],
                    "kept": rev["kept"], "removed": rev["removed"], "error": first["error"] or rev["error"]})
        print(f"[{k}/{len(picked)}] {row.asset_name}（{row.ticker}）：v5 {len(row.scenarios)} 個｜Flash v6 "
              f"{len(first['scenarios'])} → Pro 留 {len(rev['kept'])}"
              f"{'（錯誤：' + (first['error'] or rev['error'])[:60] + '）' if first['error'] or rev['error'] else ''}"
              f" ({time.time() - t0:.1f}s)", flush=True)

    json.dump(out, open(f"{OUT}/two_step_fresh.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    write_md(out)
    print(f"寫入 {OUT}/two_step_fresh.md")


def write_md(out: list, path: str = f"{OUT}/two_step_fresh.md", label: str = REVIEW_VERSION):
    n_v5 = sum(len(o["v5"]) for o in out)
    n_first = sum(len(o["first"]) for o in out)
    n_kept = sum(len(o["kept"]) for o in out)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 兩步做法外部驗證（{len(out)} 句，不在之前抽查的 94 句裡）\n\n")
        f.write(f"情境數：v5（現在資料庫）{n_v5} 個｜Flash v6 + 程式檢查 {n_first} 個 → Pro {label} 檢查後留 {n_kept} 個"
                f"（刪 {n_first - n_kept} 個、改類別 {sum(len(o.get('retyped') or []) for o in out)} 個）\n\n")
        f.write("**怎麼檢查**：每一個「留」的情境，判斷類別、方向對不對，是不是未來的變化；"
                "每一個「刪」的情境，判斷該不該刪。在方框裡打 x 代表有問題，旁邊寫原因。"
                "最後在「漏掉」寫原文有、但沒拆出來的驅動因素。\n\n")
        for i, o in enumerate(out, 1):
            f.write(f"## {i}. {o['asset']}（{o['ticker']}，{o['direction']}）\n\n")
            f.write(f"**論點**：{o['thesis']}\n\n")
            if o["evidence_quote"]:
                f.write(f"**原文引述**：{o['evidence_quote']}\n\n")
            f.write("**留下的情境**（有問題就打 x）\n\n")
            for s in o["kept"]:
                f.write(f"- [ ] {fmt(s)}\n")
            if not o["kept"]:
                f.write("- （沒有）\n")
            f.write("\n**Pro 刪掉的情境**（刪錯就打 x）\n\n")
            for s in o["removed"]:
                f.write(f"- [ ] {fmt(s)}　← {s['review_rule']} {s['review_why']}\n")
            if not o["removed"]:
                f.write("- （沒有）\n")
            if o.get("retyped"):
                f.write("\n**Pro 改類別的情境**（改錯就打 x）\n\n")
                for old, new in o["retyped"]:
                    f.write(f"- [ ] {fmt(old)}　→ 改成 {new['type']}\n")
            if o["unmapped"]:
                f.write("\n**AI 標為沒有對應類別**\n\n")
                for u in o["unmapped"]:
                    f.write(f"- {u.get('driver')}（{u.get('quote')}）\n")
            f.write("\n<details><summary>對照：現在資料庫裡的 v5 結果</summary>\n\n")
            for s in o["v5"]:
                f.write(f"- {fmt(s)}\n")
            f.write("\n</details>\n\n**漏掉**：\n\n**備註**：\n\n---\n\n")


def retry_failed(review_model: str = "gemini-3.1-pro-preview"):
    """Pro 檢查逾時的那幾句(error 有值)重跑第二步，更新 json 和 md。"""
    from apps.summaries.models import BacktestingRecord
    out = json.load(open(f"{OUT}/two_step_fresh.json", encoding="utf-8"))
    os.environ["VERTEX_LOCATION"] = "global"
    pro = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
    rows = ThesisScenarioExtraction.objects.using("summariesdb").in_bulk([o["id"] for o in out if o["error"]])
    for o in out:
        if not o["error"]:
            continue
        record = rows[o["id"]].backtest_record
        rev = review_scenarios(pro, review_model, record, o["first"], build_context(record), review_version=REVIEW_VERSION)
        o.update(kept=rev["kept"], removed=rev["removed"], error=rev["error"])
        print(f"重跑 {o['asset']}：留 {len(rev['kept'])}{'（錯誤：' + rev['error'][:60] + '）' if rev['error'] else ''}")
    json.dump(out, open(f"{OUT}/two_step_fresh.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    write_md(out)



def review_again(review_model: str = "gemini-3.1-pro-preview"):
    """沿用上一次的 Flash 結果(first)，用目前的檢查版本重做第二步，另存一份，方便跟上一版比較。"""
    from concurrent.futures import ThreadPoolExecutor
    out = json.load(open(f"{OUT}/two_step_fresh.json", encoding="utf-8"))
    pro = make_client(api_key=os.getenv("GEMINI_API_KEY", ""), location="global")
    rows = ThesisScenarioExtraction.objects.using("summariesdb").in_bulk([o["id"] for o in out])

    def work(o):
        record = rows[o["id"]].backtest_record
        rev = review_scenarios(pro, review_model, record, o["first"], build_context(record), review_version=REVIEW_VERSION)
        return {**o, "kept": rev["kept"], "removed": rev["removed"], "retyped": rev["retyped"], "error": rev["error"]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        new = list(pool.map(work, out))
    for o in new:
        print(f"{o['asset']}：{len(o['first'])} → 留 {len(o['kept'])}（改類別 {len(o['retyped'])}）"
              f"{'（錯誤：' + o['error'][:60] + '）' if o['error'] else ''}")
    json.dump(new, open(f"{OUT}/two_step_fresh_{REVIEW_VERSION}.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    write_md(new, f"{OUT}/two_step_fresh_{REVIEW_VERSION}.md")
    print(f"寫入 {OUT}/two_step_fresh_{REVIEW_VERSION}.md")


if __name__ == "__main__":
    main()
