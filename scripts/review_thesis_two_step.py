# scripts/review_thesis_two_step.py
"""
兩步做法試跑：Flash 先拆(用 compare_thesis_prompt.py 已經跑好的 v6 結果，不重新呼叫)，套用目前的程式檢查，
再請較強的模型逐一判斷每個情境要留還是刪(thesis_scenario.review_scenarios)。
用 v5 抽查(out/type_review.md)判斷過的情境驗證：✗/△ 的有沒有被刪掉、✓ 的有沒有被誤刪。
只呼叫 Gemini、不寫入資料庫。

用法：python scripts/review_thesis_two_step.py [--source out/prompt_v5_v6.json] [--model gemini-3.1-pro-preview --location global]
"""
import argparse
import json
import os
import sys
import time
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.summaries.models import ThesisScenarioExtraction
from apps.summaries.services.gemini import make_client
from apps.summaries.services.thesis_scenario import (
    REVIEW_VERSION, build_context, normalize_text, recheck_thesis_scenarios, review_scenarios,
)
from compare_thesis_prompt import fmt

OUT = "out"


def similar(a: str, b: str) -> float:
    a, b = normalize_text(a), normalize_text(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size / min(len(a), len(b))


def has(scenarios: list, target: dict) -> bool:
    return any(s["type"] == target["type"] and similar(s["quote"], target["quote"]) >= 0.6 for s in scenarios)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=f"{OUT}/prompt_v5_v6.json", help="第一步(Flash)的試跑結果")
    ap.add_argument("--model", default="gemini-3.1-pro-preview")
    ap.add_argument("--location", default="global", help="gemini-3.1-pro-preview 只在 global")
    ap.add_argument("--review", default=REVIEW_VERSION, help="檢查 prompt 版本(thesis_scenario.REVIEW_PROMPTS)")
    args = ap.parse_args()
    os.environ["VERTEX_LOCATION"] = args.location

    results = json.load(open(args.source, encoding="utf-8"))
    rows = ThesisScenarioExtraction.objects.using("summariesdb").in_bulk([r["reviewed"]["id"] for r in results])
    client = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
    out = []
    for k, r in enumerate(results, 1):
        record = rows[r["reviewed"]["id"]].backtest_record
        context = build_context(record)
        first = recheck_thesis_scenarios(record, r["new"]["ai_scenarios"])["scenarios"]
        t0 = time.time()
        rev = review_scenarios(client, args.model, record, first, context, review_version=args.review)
        target = r["reviewed"]
        out.append({**{key: r[key] for key in ("no", "mark", "note", "asset", "direction", "thesis")},
                    "reviewed": target, "first": first, "kept": rev["kept"], "removed": rev["removed"],
                    "error": rev["error"], "target_before": has(first, target), "target_after": has(rev["kept"], target)})
        print(f"[{k}/{len(results)}] #{r['no']} {r['asset']} {r['mark']}：{len(first)} → 留 {len(rev['kept'])}"
              f"{'（錯誤：' + rev['error'][:60] + '）' if rev['error'] else ''} ({time.time() - t0:.1f}s)", flush=True)

    # r1 的結果檔名沒有版本(two_step_3.1-pro-preview.*)，之後的版本加在後面
    tag = args.model.replace("gemini-", "") + ("" if args.review == "r1" else f"_{args.review}")
    json.dump(out, open(f"{OUT}/two_step_{tag}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    bad = [o for o in out if o["mark"] != "✓"]
    ok = [o for o in out if o["mark"] == "✓"]
    n_first = sum(len(o["first"]) for o in out)
    n_kept = sum(len(o["kept"]) for o in out)
    summary = (f"第一步 {n_first} 個情境 → 第二步留下 {n_kept} 個（刪掉 {n_first - n_kept} 個）；"
               f"抽查有問題的情境還在 {sum(o['target_before'] for o in bad)} → {sum(o['target_after'] for o in bad)} / {len(bad)}；"
               f"抽查正確的情境還在 {sum(o['target_before'] for o in ok)} → {sum(o['target_after'] for o in ok)} / {len(ok)}")
    print(summary)
    with open(f"{OUT}/two_step_{tag}.md", "w", encoding="utf-8") as f:
        f.write(f"# 兩步做法：Flash v6 拆 → {args.model} 檢查 {args.review}（{len(out)} 句）\n\n{summary}\n\n")
        for o in out:
            f.write(f"## #{o['no']} {o['asset']}（{o['direction']}）　v5 抽查：{o['mark']} {o['note']}\n\n")
            f.write(f"論點：{o['thesis']}\n\n抽查的情境：{fmt(o['reviewed'])}"
                    f"（第一步{'有' if o['target_before'] else '沒有'}，第二步後{'還在' if o['target_after'] else '不在'}）\n\n")
            for s in o["kept"]:
                f.write(f"- 留：{fmt(s)}\n")
            for s in o["removed"]:
                f.write(f"- ~~刪：{fmt(s)}~~　{s['review_rule']} {s['review_why']}\n")
            f.write("\n")
    print(f"寫入 {OUT}/two_step_{tag}.md")


if __name__ == "__main__":
    main()
