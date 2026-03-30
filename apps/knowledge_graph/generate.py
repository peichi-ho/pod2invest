import json
import re
import os
from datetime import datetime, timedelta, timezone

from google import genai
from django.conf import settings
from django.db import connections

from apps.summaries.models import SummaryRecord


# ==========================================
# Gemini Client
# ==========================================
_client = None


def get_client():
    global _client
    if _client is None:
        api_key = getattr(settings, "GEMINI_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "")
        _client = genai.Client(api_key=api_key)
    return _client


# ==========================================
# 模組 1: 組合摘要文字
# ==========================================
def build_summary_text(record: SummaryRecord) -> str:
    """
    將 SummaryRecord 的 one_sentence_summary, investment_takeaways, arguments
    組合成一段給 LLM 用的文字。
    """
    parts = []

    if record.one_sentence_summary:
        parts.append(f"摘要：{record.one_sentence_summary}")

    takeaways = record.investment_takeaways or {}
    bullish = takeaways.get("bullish", [])
    bearish = takeaways.get("bearish", [])
    watchlist = takeaways.get("watchlist", [])
    if bullish:
        parts.append("看多觀點：\n" + "\n".join(f"- {x}" for x in bullish))
    if bearish:
        parts.append("看空觀點：\n" + "\n".join(f"- {x}" for x in bearish))
    if watchlist:
        parts.append("值得關注：\n" + "\n".join(f"- {x}" for x in watchlist))

    for arg in (record.arguments or []):
        topic = arg.get("topic", "")
        position = arg.get("position", "")
        summary = arg.get("summary", "")
        if topic:
            block = f"【{topic}】"
            if position:
                block += f"\n立場：{position}"
            if summary:
                block += f"\n{summary}"
            parts.append(block)

    return "\n\n".join(parts)


# ==========================================
# 模組 2: 呼叫 LLM 萃取圖譜 JSON
# ==========================================
def extract_graph_from_summary(podcast_summary: str) -> str | None:
    prompt = """
    Role: 你是一位產業鏈分析專家，擅長從文本中萃取企業與產業間的「價值網圖譜」。

    Task: 請閱讀我提供的「財經 Podcast 摘要」資料。請建立 `entities` 作為節點基礎，並剖析出「公司與公司」或「公司與產業」之間的直接關聯。

    Association Rules (建立連接 Link 的條件):
    1. 垂直供應 (Supply): A 提供產品、服務或資源給 B。
    2. 趨勢共生 (Co-impact): 兩者共同受惠或受害於某一趨勢。
    3. 競爭替代 (Substitution): 兩者在同一市場競爭，或具備資源替代性。

    Output Format:
    請務必只輸出 JSON 格式的資料，用 ```json 包裝。
    必須包含:
    1. `nodes`: 包含 id, 以及 industry 屬性。
       *特別注意：請根據上下文與你的知識，為「每一個 node」獨立判斷並填寫其所屬的 `industry` (例如：半導體、金融業、電動車、航運等)。*
    2. `links`: 包含 source, target, reason, relation_type 屬性。
    """

    print("\n🧠 [階段一] 呼叫 LLM 萃取 JSON 結構資料...")
    request_content = f"{prompt}\n\n【Podcast 摘要內容】：\n{podcast_summary}"

    client = get_client()
    model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")
    response = client.models.generate_content(model=model_name, contents=request_content)
    output_text = response.text

    json_match = re.search(r'```json\n(.*?)\n```', output_text, re.DOTALL | re.IGNORECASE)
    if json_match:
        print("✅ [階段一] 成功萃取 JSON。")
        return json_match.group(1)

    print("❌ [階段一] 找不到 JSON 區塊，嘗試直接解析...")
    try:
        json.loads(output_text)
        return output_text
    except Exception:
        print("❌ 原始輸出非有效 JSON：")
        print(output_text)
        return None


# ==========================================
# 模組 3: 重複判斷
# ==========================================
def _node_exists(cursor, name: str) -> bool:
    cursor.execute("SELECT 1 FROM nodes WHERE name = %s LIMIT 1", [name])
    return cursor.fetchone() is not None


def _is_similar_reason(reason1: str, reason2: str) -> bool:
    """用 Gemini 判斷兩段 reason 是否語意相近，只回傳 YES / NO。"""
    prompt = (
        "以下兩段描述，是否在表達相同或高度相似的意思？請只回答 YES 或 NO，不要解釋。\n\n"
        f"描述一：{reason1}\n"
        f"描述二：{reason2}"
    )
    client = get_client()
    model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")
    response = client.models.generate_content(model=model_name, contents=prompt)
    return response.text.strip().upper().startswith("YES")


def _link_duplicate_exists(cursor, source: str, target: str, reason: str) -> bool:
    """
    七天內是否已存在 source + target 相同且 reason 語意相近的 link。
    有任何一筆符合就視為重複。
    """
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).date()
    cursor.execute(
        "SELECT reason FROM links WHERE source = %s AND target = %s AND summary_date >= %s",
        [source, target, seven_days_ago],
    )
    rows = cursor.fetchall()
    for (existing_reason,) in rows:
        if _is_similar_reason(reason, existing_reason):
            return True
    return False


# ==========================================
# 模組 4: 寫入 Supabase (knowledge_graphdb)
# ==========================================
def save_to_db(json_data_str: str, summary_date: str, podcast_source: str):
    try:
        data = json.loads(json_data_str)
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析失敗: {e}")
        return

    nodes_inserted = 0
    links_inserted = 0

    with connections["knowledge_graphdb"].cursor() as cursor:

        # --- Nodes ---
        for node in data.get("nodes", []):
            name = node.get("id") or node.get("name")
            industry = node.get("industry", "未分類")
            if not name:
                continue
            if _node_exists(cursor, name):
                print(f"  ⏭️  node 已存在，跳過：{name}")
            else:
                cursor.execute(
                    "INSERT INTO nodes (name, industry) VALUES (%s, %s)",
                    [name, industry],
                )
                nodes_inserted += 1

        # --- Links ---
        for link in data.get("links", []):
            source = link.get("source")
            target = link.get("target")
            relation_type = link.get("relation_type", "Unknown")
            reason = link.get("reason", "")
            if not source or not target:
                continue
            if _link_duplicate_exists(cursor, source, target, reason):
                print(f"  ⏭️  link 重複，跳過：{source} → {target}")
            else:
                cursor.execute(
                    """
                    INSERT INTO links (source, target, relation_type, reason, summary_date, podcast_source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [source, target, relation_type, reason, summary_date, podcast_source],
                )
                links_inserted += 1

    print(f"✅ [資料庫] 完成！新增 {nodes_inserted} 個節點，{links_inserted} 條連線。")


# ==========================================
# 模組 5: Pipeline
# ==========================================
def process_all_summaries():
    """
    從 summariesdb 讀取所有 SummaryRecord，
    依序萃取知識圖譜並寫入 knowledge_graphdb。
    """
    records = SummaryRecord.objects.using("summariesdb").all().order_by("created_at")
    total = records.count()
    print(f"\n📚 共 {total} 筆摘要待處理")

    for i, record in enumerate(records, 1):
        print(f"\n[{i}/{total}] 處理摘要 #{record.id}: {record.source_filename or '(無檔名)'}")
        summary_text = build_summary_text(record)
        summary_date = record.created_at.date().isoformat()
        podcast_source = record.source_filename or f"record-{record.id}"

        extracted_json = extract_graph_from_summary(summary_text)
        if extracted_json:
            save_to_db(
                json_data_str=extracted_json,
                summary_date=summary_date,
                podcast_source=podcast_source,
            )


def process_new_podcast(summary_text: str, summary_date: str, podcast_source: str):
    """處理單一筆摘要文字（供 views.py 手動觸發使用）。"""
    print(f"\n🚀 處理摘要：{podcast_source} ({summary_date})")
    extracted_json = extract_graph_from_summary(summary_text)
    if extracted_json:
        save_to_db(
            json_data_str=extracted_json,
            summary_date=summary_date,
            podcast_source=podcast_source,
        )
