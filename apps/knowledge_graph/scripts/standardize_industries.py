"""
一次性腳本：把 KG_DB nodes 表的 industry 欄位標準化為 16 個固定分類。

執行方式：
    python scripts/standardize_industries.py
"""
import os
import sys
import json
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import connections
from django.conf import settings
import google.genai as genai

# ── 16 個標準分類 ──────────────────────────────────────────────────────────────
STANDARD_INDUSTRIES = [
    "半導體",
    "電子/硬體",
    "AI/軟體/雲端",
    "金融",
    "ETF/基金",
    "能源",
    "汽車/電動車",
    "生技/醫療",
    "消費/零售",
    "工業/製造",
    "媒體/娛樂",
    "航太/國防",
    "房地產/建設",
    "電信/網通",
    "航運/物流",
    "其他",
]

CATEGORIES_TEXT = "\n".join(f"- {s}" for s in STANDARD_INDUSTRIES)


def fetch_distinct_industries() -> list[str]:
    with connections["knowledge_graphdb"].cursor() as cur:
        cur.execute(
            "SELECT DISTINCT industry FROM nodes "
            "WHERE industry IS NOT NULL AND industry != '' "
            "ORDER BY industry"
        )
        return [row[0] for row in cur.fetchall()]


def classify_with_gemini(industries: list[str]) -> dict[str, str]:
    """送給 Gemini 批次分類，回傳 {舊標籤: 新標籤} 的 dict。"""
    client = genai.Client(
        vertexai=True,
        project=settings.VERTEX_PROJECT_ID,
        location=settings.VERTEX_LOCATION,
    )
    model  = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")

    industries_json = json.dumps(industries, ensure_ascii=False)
    prompt = f"""你是一個產業分類專家。
以下是 {len(industries)} 個由 AI 生成的公司產業標籤（可能重複、錯誤或過於細碎）：

{industries_json}

請將每個標籤對應到以下 16 個標準分類之一：
{CATEGORIES_TEXT}

對應規則：
- 若標籤描述的是國家、地區、總體經濟、政策、未知等非產業概念，歸入「其他」
- 若標籤描述的是 ETF、指數、基金、股票等金融工具，歸入「ETF/基金」
- 金融機構（銀行、保險、券商）歸入「金融」
- 航運、物流、倉儲、造船、港口歸入「航運/物流」
- 只輸出 JSON 物件，key 為原始標籤，value 為對應的標準分類名稱（只能用上面 16 個之一）
- 不要輸出任何其他文字或 markdown
"""

    resp = client.models.generate_content(model=model, contents=prompt)
    text = (getattr(resp, "text", "") or "").strip()

    # 移除可能的 markdown code block
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            l for l in lines
            if not l.strip().startswith("```")
        )

    try:
        mapping = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON parse failed: {e}")
        print("Raw response:", text[:500])
        raise

    # 驗證所有 value 都在標準分類裡
    invalid = {k: v for k, v in mapping.items() if v not in STANDARD_INDUSTRIES}
    if invalid:
        print(f"[WARN] {len(invalid)} invalid mappings, forcing to '其他':")
        for k, v in list(invalid.items())[:10]:
            print(f"  {k!r} → {v!r}")
        for k in invalid:
            mapping[k] = "其他"

    return mapping


def update_nodes(mapping: dict[str, str]) -> int:
    """批次 UPDATE nodes 表，回傳更新筆數。"""
    total = 0
    with connections["knowledge_graphdb"].cursor() as cur:
        # 用 CASE WHEN 一次更新所有 rows，避免 N 次 UPDATE
        when_clauses = " ".join(
            f"WHEN industry = %s THEN %s"
            for _ in mapping
        )
        params = []
        for old, new in mapping.items():
            params.extend([old, new])

        sql = f"""
            UPDATE nodes
            SET industry = CASE {when_clauses} ELSE industry END
            WHERE industry IN ({', '.join(['%s'] * len(mapping))})
        """
        params.extend(list(mapping.keys()))
        cur.execute(sql, params)
        total = cur.rowcount

    return total


def main():
    print("1. 讀取所有不重複的 industry 標籤…")
    industries = fetch_distinct_industries()
    print(f"   共 {len(industries)} 個標籤")

    print("\n2. 送 Gemini 批次分類…")
    mapping = classify_with_gemini(industries)
    print(f"   拿到 {len(mapping)} 個對應")

    # 顯示分類結果統計
    from collections import Counter
    dist = Counter(mapping.values())
    print("\n   分類分布：")
    for cat, cnt in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"   {cnt:4d}  {cat}")

    print("\n3. 確認後更新資料庫（輸入 yes 繼續）…")
    ans = input("   > ").strip().lower()
    if ans != "yes":
        print("   已取消。")
        return

    print("\n4. 更新 nodes 表…")
    updated = update_nodes(mapping)
    print(f"   完成，共更新 {updated} 筆 nodes")

    # 清掉暫存檔
    try:
        os.remove("industry_list.txt")
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    main()
