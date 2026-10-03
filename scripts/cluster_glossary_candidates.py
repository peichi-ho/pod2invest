# scripts/cluster_glossary_candidates.py
"""
把glossary候選清單(new_candidates_high_confidence)裡「字串互相包含」的詞彙群組
起來(用Union-Find把兩兩配對串成連通元件，不是只看pair，例如S&P 500/S&P 500指數/
S&P500/S&P500指數這4個會被串成同一群，不是分成3個獨立pair)，每群自動建議一個
「主要詞條」(seen_count最高的那個)，其餘變成alias候選。

只是提出建議、不直接寫資料庫——群組裡有些可能語意上不該合併(例如「移動平均線」
vs「200日移動平均線」，一個是通用概念一個是特定參數)，這種情況留給人工review時
自己判斷要不要真的合併，這支腳本只負責把「看起來像的」歸類到一起方便一次看完，
不做語意判斷。

用法：python scripts/cluster_glossary_candidates.py
"""
import json

IN_PATH = "/tmp/glossary_candidates_v2.json"
OUT_PATH = "/tmp/glossary_clusters.json"


def main():
    data = json.load(open(IN_PATH))
    candidates = data["new_candidates_high_confidence"]
    by_term = {c["term"]: c for c in candidates}
    terms = list(by_term.keys())

    # 原本用Union-Find做遞移閉包，結果連鎖爆炸：只要A是B的子字串、B又是C的子字串，
    # 即使A跟C語意上完全無關也會被合併(實測"供應鏈"跟"半導體"被"半導體供應鏈"這個
    # 複合詞橋接在一起、"GDP"跟"CPI"被"年增率"這個共用後綴橋接在一起，都是明顯不該
    # 合併的假陽性)。改成不做遞移：只找「最短的那個詞」當錨點，把直接包含它的其他
    # 詞組成一群，錨點本身不能是其他更短錨點的超集(避免兩層以上的鏈接)——每群最多
    # 一層，不會再出現A-B-C隔代語意不相關卻被合併的問題。
    is_anchor = {}
    for t in terms:
        if len(t) < 3:
            is_anchor[t] = False
            continue
        is_anchor[t] = not any(
            t != t2 and len(t2) >= 3 and t2 in t and len(t2) / len(t) >= 0.5
            for t2 in terms
        )

    groups = {t: [t] for t in terms if is_anchor[t]}
    for t in terms:
        if is_anchor[t]:
            continue
        candidates_anchors = [
            a for a in groups
            if a in t and len(a) / len(t) >= 0.5
        ]
        if not candidates_anchors:
            groups[t] = [t]
            continue
        # 可能同時符合好幾個錨點(罕見)，選字面上最長的那個錨點(重疊程度最高)
        best_anchor = max(candidates_anchors, key=len)
        groups[best_anchor].append(t)

    clusters = [members for members in groups.values() if len(members) > 1]
    clusters.sort(key=lambda members: -max(by_term[m]["seen_count"] for m in members))

    singleton_count = sum(1 for members in groups.values() if len(members) == 1)

    print(f"候選總數：{len(candidates)}")
    print(f"群組數(2筆以上互相包含)：{len(clusters)}")
    print(f"群組涉及的詞彙數：{sum(len(c) for c in clusters)}")
    print(f"完全獨立、沒有重疊的詞彙數：{singleton_count}")
    print()

    out_clusters = []
    for members in clusters:
        members_sorted = sorted(members, key=lambda m: -by_term[m]["seen_count"])
        canonical = members_sorted[0]
        out_clusters.append({
            "suggested_canonical": canonical,
            "members": [
                {
                    "term": m,
                    "seen_count": by_term[m]["seen_count"],
                    "category": by_term[m]["category"],
                    "short_definition": by_term[m]["short_definition"],
                }
                for m in members_sorted
            ],
        })

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out_clusters, f, ensure_ascii=False, indent=2)
    print(f"群組結果存到: {OUT_PATH}")

    print()
    print("=" * 70)
    print("前30組(依最高seen_count排序)")
    print("=" * 70)
    for c in out_clusters[:30]:
        print(f"→ 建議主要詞條: {c['suggested_canonical']!r}")
        for m in c["members"]:
            print(f"    {m['term']!r:30} seen={m['seen_count']:<4} [{m['category']}]")
        print()


if __name__ == "__main__":
    main()
