# scripts/compare_thesis_prompt.py
"""
新版 prompt 試跑：拿 v5 抽樣檢查(out/type_review.md，94 句)裡判斷為 ✗、△ 的論點，加幾句 ✓ 的
(確認沒有把原本對的改壞)，用新版 prompt 重拆一次，跟 v5 並排輸出到 out/。
只呼叫 Gemini、不寫入資料庫(不呼叫 save_extraction)。

用法：python scripts/compare_thesis_prompt.py [--version v6] [--n-ok 6] [--model gemini-3.1-pro-preview --location global]
指定 --model 時輸出檔名會加上模型名稱，不會蓋掉預設模型(Flash)的結果。
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.summaries.models import ThesisScenarioExtraction
from apps.summaries.services.gemini import make_client
from apps.summaries.services.thesis_scenario import SCENARIO_TYPES, extract_thesis_scenarios

OUT = "out"
BASE_VERSION = "v5"      # 比較基準：之前抽查過的 v5 結果
TYPE_ZH = {k: v["name"].split("（")[0].split("(")[0] for k, v in SCENARIO_TYPES.items()}


def load_review():
    """out/type_review.md 每一列的編號 → (判斷符號, 說明)；編號對應 type_review_sample.json 的順序。"""
    marks = {}
    for line in open(f"{OUT}/type_review.md", encoding="utf-8"):
        m = re.match(r"\|\s*(\d+)\s*\|.*\|\s*([✓△✗])\s*(.*?)\s*\|\s*$", line)
        if m:
            marks[int(m.group(1))] = (m.group(2), m.group(3))
    sample = json.load(open(f"{OUT}/type_review_sample.json", encoding="utf-8"))
    return [(i, s, *marks[i]) for i, s in enumerate(sample, 1) if i in marks]


def fmt(s: dict) -> str:
    extra = []
    if s.get("figures") and any(v is not None for v in s["figures"].values()):
        extra.append(", ".join(f"{k}={v}" for k, v in s["figures"].items() if v is not None and k != "year_inferred"))
    if "margin_drag" in s:
        extra.append("稀釋毛利" if s["margin_drag"] else "不稀釋毛利")
    if "cost_kind" in s:
        extra.append({"cogs": "生產成本", "opex": "營業費用", "both": "兩者"}[s["cost_kind"]])
    if s.get("duration") == "short":
        extra.append("短期")
    tail = f"（{'；'.join(extra)}）" if extra else ""
    return f"{TYPE_ZH.get(s['type'], s['type'])}{'↑' if s.get('effect') == 'up' else '↓' if s.get('effect') == 'down' else ''}：{s['quote']}{tail}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v6")
    ap.add_argument("--n-ok", type=int, default=6, help="另外放幾句原本判斷 ✓ 的")
    ap.add_argument("--model", default=None, help="不給就用 THESIS_SCENARIO_MODEL(預設 gemini-2.5-flash)")
    ap.add_argument("--location", default=None,
                    help="Vertex 區域；gemini-3.1-pro-preview 只在 global。.env 會蓋掉命令列的環境變數，所以用參數指定")
    args = ap.parse_args()
    if args.location:
        os.environ["VERTEX_LOCATION"] = args.location
    tag = f"{args.version}_{args.model.replace('gemini-', '')}" if args.model else args.version

    review = load_review()
    bad = [r for r in review if r[2] in "✗△"]
    ok = [r for r in review if r[2] == "✓"][:: max(1, len([r for r in review if r[2] == "✓"]) // args.n_ok)][: args.n_ok]
    picked, seen = [], set()
    for r in bad + ok:
        if r[1]["id"] not in seen:
            seen.add(r[1]["id"])
            picked.append(r)
    rows = ThesisScenarioExtraction.objects.using("summariesdb").in_bulk([r[1]["id"] for r in picked])

    client = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
    model = args.model or os.getenv("THESIS_SCENARIO_MODEL", "gemini-2.5-flash")
    results = []
    for k, (no, s, mark, note) in enumerate(picked, 1):
        row = rows[s["id"]]
        record = row.backtest_record
        t0 = time.time()
        new = extract_thesis_scenarios(client, model, record, prompt_version=args.version)
        results.append({"no": no, "mark": mark, "note": note, "ticker": row.ticker, "asset": row.asset_name,
                        "direction": record.direction, "thesis": record.thesis,
                        "reviewed": s, "old": row.scenarios, "new": new})
        print(f"[{k}/{len(picked)}] #{no} {row.asset_name} {mark} → {new['status']} "
              f"{len(new['scenarios'])} 個情境 ({time.time() - t0:.1f}s)", flush=True)

    json.dump(results, open(f"{OUT}/prompt_{BASE_VERSION}_{tag}.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    with open(f"{OUT}/prompt_{BASE_VERSION}_{tag}.md", "w", encoding="utf-8") as f:
        f.write(f"# {BASE_VERSION} vs {args.version} 並排（{len(results)} 句）\n\n")
        for r in results:
            f.write(f"## #{r['no']} {r['asset']}（{r['direction']}）　{BASE_VERSION} 抽查：{r['mark']} {r['note']}\n\n")
            f.write(f"論點：{r['thesis']}\n\n")
            f.write(f"- 抽查的情境：{fmt(r['reviewed'])}\n")
            f.write(f"- **{BASE_VERSION}**（{len(r['old'])} 個）：\n")
            for s in r["old"]:
                f.write(f"  - {fmt(s)}\n")
            f.write(f"- **{args.version}**（{len(r['new']['scenarios'])} 個）：\n")
            for s in r["new"]["scenarios"]:
                f.write(f"  - {fmt(s)}\n")
            for u in r["new"].get("unmapped") or []:
                f.write(f"  - 沒有對應類別：{u.get('driver')}（{u.get('quote')}）\n")
            for d in r["new"]["dropped"]:
                f.write(f"  - ~~{TYPE_ZH.get(d.get('type'), d.get('type'))}：{d.get('quote')}~~ 程式丟掉：{d['reason']}\n")
            f.write("\n")
    print(f"寫入 {OUT}/prompt_{BASE_VERSION}_{tag}.md")


if __name__ == "__main__":
    main()
