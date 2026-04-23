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
    同時將 entities.companies_or_stocks 組成白名單，限制 LLM 生成的節點來源。
    """
    parts = []

    # 白名單：從 entities 抽出公司與股票清單
    entities = record.entities or {}
    companies = entities.get("companies_or_stocks", [])
    if companies:
        parts.append(
            "【本集提及的公司與股票（nodes 的 id 優先來自此清單；若連線對象是產業趨勢而非特定公司，可使用產業名稱作為節點）】：\n"
            + "、".join(companies)
        )

    if record.one_sentence_summary:
        parts.append(f"摘要：{record.one_sentence_summary}")

    takeaways = record.investment_takeaways or {}
    bullish   = takeaways.get("bullish", [])
    bearish   = takeaways.get("bearish", [])
    watchlist = takeaways.get("watchlist", [])
    if bullish:
        parts.append("看多觀點：\n" + "\n".join(f"- {x}" for x in bullish))
    if bearish:
        parts.append("看空觀點：\n" + "\n".join(f"- {x}" for x in bearish))
    if watchlist:
        parts.append("值得關注：\n" + "\n".join(f"- {x}" for x in watchlist))

    for arg in (record.arguments or []):
        topic    = arg.get("topic", "")
        position = arg.get("position", "")
        summary  = arg.get("summary", "")
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

    Task: 請閱讀我提供的「財經 Podcast 摘要」資料，剖析出「公司與公司」或「公司與產業」之間的直接關聯。

    【節點（Node）規則】
    ✅ 合格的節點：
      - 具體公司名稱（如 台積電、Apple、鴻海）
      - 股票代號（如 NVDA、0050）
      - ETF 名稱
      - 明確產業別（如 半導體、金融業、電動車、航運）
    ❌ 以下絕對不能作為節點：
      - 人物（川普、黃仁勳、馬斯克 等）
      - 抽象趨勢或概念（AI熱潮、AI獲利化、升息循環、關稅政策）
      - 事件（中期選舉、關稅戰）
      - 風險概念（地緣政治風險、泡沫）
      - 國家或地區（美國、中國、東南亞）
      - 總經指標（VIX、通膨、利率、匯率）
    節點的 id 必須優先使用摘要開頭【本集提及的公司與股票】清單中的名稱。

    【連線（Link）規則】
    ✅ source 和 target 都必須是合格節點（公司或明確產業）
    ✅ reason 必須描述具體的商業行為或產業關係，例如：
      - 「A 委託 B 代工晶片」
      - 「A 與 B 共同受惠於 AI 資料中心建設潮」
      - 「A 正在搶佔 B 的市場份額」
    ❌ 以下是不合格的 reason，不應建立此類連線：
      - 「兩者都受到關稅影響」（原因太模糊，沒有直接商業關係）
      - 「都是科技公司」（不是具體商業關係）
      - 「都被 Podcast 提及」

    Association Rules (relation_type 只能從以下三種選一):
    1. Supply      : A 提供產品、服務或資源給 B（垂直供應）
    2. Co-impact   : 兩者共同受惠或受害於同一具體商業趨勢（趨勢共生）
    3. Substitution: 兩者在同一市場直接競爭或互相替代（競爭替代）

    【Industry 屬性規則】
    請為每個 node 填寫 industry，優先從以下清單選擇最接近的：
    半導體、電子業、軟體、雲端服務、AI/人工智慧、網路/電商、金融業、
    保險業、電動車、傳統汽車、航運、能源/石油、再生能源、生技/醫療、
    消費品、零售業、食品業、電信業、媒體/娛樂、房地產、原物料、ETF/基金
    若以上皆不適合，可自行填寫簡短的產業名稱（4字以內）。

    Output Format:
    請務必只輸出 JSON 格式的資料，用 ```json 包裝。
    必須包含:
    1. `nodes`: 包含 id（公司或產業名稱）以及 industry 屬性。
    2. `links`: 包含 source, target, reason, relation_type 屬性。
       ❌ source 與 target 絕對不能是同一個節點。
       ❌ reason 不能是「XXX屬於某產業」、「XXX隸屬於某行業」這類分類描述，必須是具體商業行為或產業關係。
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
def _resolve_node_name(llm_name: str, existing_names: list) -> str:
    """
    用 Gemini 判斷 llm_name 是否與 existing_names 中某個名稱代表同一實體。
    若有匹配，回傳 existing_names 中的正規名稱；
    否則回傳 llm_name 本身（代表全新節點）。
    每呼叫一次會動態比對當下的 existing_names（含本批次已新增的節點）。
    """
    if not existing_names:
        return llm_name

    prompt = (
        f"新名稱：「{llm_name}」\n\n"
        "現有名稱清單：\n"
        + "\n".join(f"- {n}" for n in existing_names)
        + "\n\n"
        "若新名稱與清單中某個名稱代表同一家公司或產業，"
        "請只回覆那個清單中的名稱（必須與清單完全一致）。"
        "若清單中沒有符合的，請只回覆 NONE。不要解釋。"
    )
    try:
        client = get_client()
        model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")
        response = client.models.generate_content(model=model_name, contents=prompt)
        result = response.text.strip()
        if result != "NONE" and result in existing_names:
            return result
    except Exception as e:
        print(f"⚠️ 名稱解析失敗（{llm_name}），使用原始名稱。錯誤: {e}")
    return llm_name


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

        # 載入現有所有 nodes 作為正規名稱基準
        cursor.execute("SELECT name, industry FROM nodes")
        existing_nodes: dict = {row[0]: row[1] for row in cursor.fetchall()}
        # existing_names 會隨本批次新增而動態擴充，實現批次內去重
        existing_names: list = list(existing_nodes.keys())

        # ── 階段一：建立 llm名稱 → 正規名稱 對照表，並插入真正新的 nodes ──
        name_map: dict = {}

        for node in data.get("nodes", []):
            llm_name = node.get("id") or node.get("name")
            industry = node.get("industry", "未分類")
            if not llm_name:
                continue

            canonical = _resolve_node_name(llm_name, existing_names)
            name_map[llm_name] = canonical

            if canonical not in existing_nodes:
                cursor.execute(
                    "INSERT INTO nodes (name, industry) VALUES (%s, %s)",
                    [canonical, industry],
                )
                existing_nodes[canonical] = industry
                existing_names.append(canonical)
                nodes_inserted += 1
                print(f"  ✅ 新增 node：{canonical} ({industry})")
            else:
                print(f"  ⏭️  node 已存在或合併至：{canonical}（原始：{llm_name}）")

        # ── 階段二 & 三：替換 source/target 為正規名稱，再插入 links ──
        all_nodes = set(existing_nodes.keys())

        for link in data.get("links", []):
            llm_source = link.get("source")
            llm_target = link.get("target")
            relation_type = link.get("relation_type", "Unknown")
            reason = link.get("reason", "")

            if not llm_source or not llm_target:
                continue

            source = name_map.get(llm_source, llm_source)
            target = name_map.get(llm_target, llm_target)

            if source == target:
                print(f"  ⏭️  跳過自環 link：{source} → {target}")
                continue

            if source not in all_nodes or target not in all_nodes:
                print(f"  ⚠️  跳過 link（節點不存在）：{source} → {target}")
                continue
                # 確保 source 和 target 節點存在，否則自動補建
            for node_name in [source, target]:
                if not _node_exists(cursor, node_name):
                    cursor.execute(
                    "INSERT INTO nodes (name, industry) VALUES (%s, %s)",
                    [node_name, "未分類"],
                )

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
    records = SummaryRecord.objects.using("summariesdb").defer("outlook_calls").order_by("created_at")
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
