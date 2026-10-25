# scripts/apply_glossary_candidates.py
"""
把審核過的glossary候選(new_candidates_high_confidence，2427筆)寫進GlossaryTerm/
GlossaryAlias——這是extract_glossary_candidates.py docstring裡提到、原本規劃要
另外寫的「apply」腳本，現在補上。

審核結論(2026-10-01，見對話紀錄)：
  1. 用字串重疊分群找出177組「長得像」的候選，逐組人工判斷後，只有17組是真正
     單純的命名/格式差異(例如VIX/VIX指數/VIX 指數)，這17組合併成一個詞條，
     其餘member變成alias。
  2. 其餘看起來像但其實是不同概念的(例如DDR4 vs DDR5、GDP vs 人均GDP、
     PCE vs 核心PCE)，維持各自獨立的詞條，不合併。
  3. 每筆候選本身在萃取階段就帶有aliases欄位(不是這次分群產生的，是LLM抽取
     時就判斷出的同義詞，例如GPU的alias有"Graphics Processing Unit"、
     "圖形處理器")，一併寫入。
  4. long_definition故意留空——完整長定義留到之後另外一輪再生成，這次只收錄
     term/short_definition/category/aliases。

用法：
  python scripts/apply_glossary_candidates.py --dry-run   # 只印會怎麼寫，不寫入
  python scripts/apply_glossary_candidates.py              # 真的寫入資料庫
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.glossary.models import GlossaryTerm, GlossaryAlias

CANDIDATES_PATH = "/tmp/glossary_candidates_v2.json"
MERGE_VERDICTS_PATH = "/tmp/glossary_merge_verdicts_v3.json"

# 人工review確認過的17組合併(canonical <- member)，直接寫死在這裡而不是重新
# 跑一次heuristic，避免之後candidates檔案有變動、heuristic微調又跑出不同結果，
# 這17組是已經拍板定案的名單。
CONFIRMED_MERGES = [
    ("技術面", ["技術面指標"]),
    ("VIX指數", ["VIX", "VIX 指數"]),
    ("標普500指數", ["標普500"]),
    ("PCE", ["PCE指數"]),
    ("PMI", ["PMI指數"]),
    ("納斯達克指數", ["納斯達克"]),
    ("那斯達克指數", ["那斯達克"]),
    ("羅素2000指數", ["羅素2000"]),
    ("S&P 500指數", ["S&P 500", "S&P 500 指數"]),
    ("MSCI", ["MSCI指數"]),
    ("S&P500指數", ["S&P500"]),
    ("OTC指數", ["OTC"]),
    ("市值加權指數", ["市值加權"]),
    ("SCFI指數", ["SCFI"]),
    ("新訂單", ["新訂單指數"]),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = json.load(open(CANDIDATES_PATH))
    candidates = data["new_candidates_high_confidence"]
    by_term = {c["term"]: c for c in candidates}

    merged_away = set()  # 會變成alias、不會單獨成term的詞彙
    canonical_extra_aliases = {}  # canonical term -> 額外要加的alias清單(來自合併)
    for canonical, members in CONFIRMED_MERGES:
        canonical_extra_aliases[canonical] = list(members)
        for m in members:
            merged_away.add(m)

    # 一次把現有的term名稱全部撈出來查表比對，不要對2427筆candidate各自查一次DB
    # (逐筆查太慢，DB在遠端，光是來回延遲加起來就要跑很久)。
    existing_terms = set(GlossaryTerm.objects.values_list("term", flat=True))

    to_create = []       # (term_str, cand, alias_set) 準備要建的term
    skipped_existing = 0

    for term_str, cand in by_term.items():
        if term_str in merged_away:
            continue  # 這個詞彙會在它的canonical term底下變成alias，不單獨建term
        if term_str in existing_terms:
            skipped_existing += 1
            continue
        alias_set = set(cand.get("aliases") or [])
        alias_set.discard(term_str)
        alias_set.update(canonical_extra_aliases.get(term_str, []))
        alias_set = {a for a in alias_set if a and a != term_str}
        to_create.append((term_str, cand, alias_set))

    total_aliases = sum(len(a) for _, _, a in to_create)

    print(f"{'(dry-run，沒有真的寫入)' if args.dry_run else '準備寫入資料庫'}")
    print(f"會建立的GlossaryTerm數: {len(to_create)}")
    print(f"會建立的GlossaryAlias數: {total_aliases}")
    print(f"因為term已存在而跳過的數: {skipped_existing}")
    print(f"合併掉(變成alias、沒有單獨建term)的詞彙數: {len(merged_away)}")

    if args.dry_run or not to_create:
        return

    # bulk_create一次寫入，不要2427筆一筆一筆create(逐筆RPC太慢)——Postgres的
    # bulk_create預設會用RETURNING拿回每筆的pk，接下來建alias才能對得上term_id。
    term_objs = GlossaryTerm.objects.bulk_create([
        GlossaryTerm(term=term_str, short_definition=cand["short_definition"], category=cand.get("category", ""))
        for term_str, cand, _ in to_create
    ])
    created_terms = len(term_objs)

    alias_objs = []
    for term_obj, (term_str, cand, alias_set) in zip(term_objs, to_create):
        for alias in alias_set:
            alias_objs.append(GlossaryAlias(term=term_obj, alias=alias))
    GlossaryAlias.objects.bulk_create(alias_objs, ignore_conflicts=True)
    created_aliases = len(alias_objs)

    print()
    print(f"已寫入：{created_terms} 筆GlossaryTerm，{created_aliases} 筆GlossaryAlias")


if __name__ == "__main__":
    main()
