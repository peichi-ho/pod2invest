import json
import re
import os
import time
import unicodedata
import concurrent.futures
from datetime import datetime, timedelta, timezone
from typing import Optional

from google import genai
from google.genai import errors as genai_errors
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
        _client = genai.Client(
            vertexai=True,
            project=settings.VERTEX_PROJECT_ID,
            location=settings.VERTEX_LOCATION,
        )
    return _client


_GEMINI_TIMEOUT = 90  # 秒，單次 API 呼叫超過此時間視為 hang

# 429 RESOURCE_EXHAUSTED（配額耗盡）跟其他錯誤性質不同：它不代表程式有問題，
# 純粹是「這段時間用太多，等配額回補就會恢復」，所以給它自己一組獨立、更有
# 耐心的重試預算，不跟 max_retries 共用。這樣長時間跑的大批次（例如一次處理
# 幾百集逐字稿）遇到配額限制時會自動睡過冷卻期、睡醒繼續跑，不需要人發現
# 「腳本停了」之後再手動重新執行。
# 20 次、每次等待隨次數遞增但上限 600 秒，總計最長可以自動撐約 2.4 小時的
# 配額冷卻（多數常見的分鐘/小時級配額都在這範圍內會自動恢復；如果配額是
# 天級的重置週期，20 次仍會耗盡並往上拋出，交由外層（例如 process_all_episodes
# 逐集的 try/except）跳過這一筆，下次重新執行整個批次時會自動接續處理）。
_QUOTA_MAX_RETRIES = 20
_QUOTA_WAIT_CAP = 600


def _call_gemini_with_retry(
    contents: str,
    max_retries: int = 5,
    temperature: float = 0.0,
    response_schema: Optional[dict] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
) -> str:
    """
    呼叫 Gemini API。
    - 超過 90 秒沒回應（hang）：重試 max_retries 次，等待 30 → 60 → 90 秒...
    - 429 RESOURCE_EXHAUSTED：獨立重試 _QUOTA_MAX_RETRIES 次，等待 60 → 120 → ...
      → 上限 _QUOTA_WAIT_CAP 秒，不佔用 max_retries（見上方常數說明）
    - 其他錯誤：重試 max_retries 次，等待 2 → 4 → 8 秒...

    temperature 預設 0：這個模組所有呼叫都是抽取/分類/是非判斷，不需要創意，
    低溫度讓同一份輸入的輸出更穩定可重現（跟專案其他 Gemini 呼叫點一致，
    例如 sentiment_score.py、outlook_review.py 也都是分類任務用 temperature=0）。

    response_schema：帶入時會用 Gemini 原生的 response_schema 強制輸出符合
    指定 JSON 結構（含 enum 限制），取代「prompt 裡用文字要求格式 + 事後
    regex 從 ```json 區塊挖出來解析」的作法，從源頭杜絕格式跑掉或
    relation_type 打錯字這類問題。

    model：不給就用 settings.GEMINI_MODEL；需要較強模型的步驟（例如 extract_v2 的
    關係判斷）可以個別指定。

    max_output_tokens：限制輸出長度。小模型搭配 response_schema 偶爾會陷入無限重複
    同一筆資料的迴圈，沒有上限時要等到模型輸出上限才結束，常常因此超過
    _GEMINI_TIMEOUT 被判定超時；有上限就會很快回傳（JSON 可能被截斷，由呼叫端處理）。
    """

    client = get_client()
    model_name = model or getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")

    config: dict = {"temperature": temperature}
    if max_output_tokens:
        config["max_output_tokens"] = max_output_tokens
    if response_schema is not None:
        config["response_mime_type"] = "application/json"
        config["response_schema"] = response_schema

    attempt = 0        # 一般錯誤 / hang 的重試計數（上限 max_retries）
    quota_attempt = 0  # 429 配額錯誤的重試計數（獨立上限 _QUOTA_MAX_RETRIES）

    while True:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(
                client.models.generate_content,
                model=model_name, contents=contents, config=config,
            )
            response = future.result(timeout=_GEMINI_TIMEOUT)
            executor.shutdown(wait=False)
            return response.text

        except concurrent.futures.TimeoutError:
            executor.shutdown(wait=False)  # 不等背景執行緒，直接放棄
            attempt += 1
            if attempt >= max_retries:
                raise TimeoutError(f"Gemini API 連續 {max_retries} 次超時（{_GEMINI_TIMEOUT}s）")
            wait = 30 * attempt
            print(f"  ⚠️  Gemini API 超時（第 {attempt} 次），{wait}s 後重試...")
            time.sleep(wait)

        except genai_errors.ClientError as e:
            executor.shutdown(wait=False)
            if e.code == 429:
                quota_attempt += 1
                if quota_attempt >= _QUOTA_MAX_RETRIES:
                    raise
                wait = min(60 * quota_attempt, _QUOTA_WAIT_CAP)
                print(f"  ⏸️  Gemini API 429 配額耗盡（第 {quota_attempt}/{_QUOTA_MAX_RETRIES} 次），{wait}s 後自動重試...")
                time.sleep(wait)
            else:
                attempt += 1
                if attempt >= max_retries:
                    raise
                wait = 2 ** attempt
                print(f"  ⚠️  Gemini API 錯誤（第 {attempt} 次），{wait}s 後重試。錯誤：{e}")
                time.sleep(wait)

        except Exception as e:
            executor.shutdown(wait=False)
            attempt += 1
            if attempt >= max_retries:
                raise
            wait = 2 ** attempt
            print(f"  ⚠️  Gemini API 失敗（第 {attempt} 次），{wait}s 後重試。錯誤：{e}")
            time.sleep(wait)


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

# 圖譜抽取的輸出結構，用 Gemini 原生 response_schema 強制輸出符合此格式
# （含 relation_type 的 enum 限制），取代原本「prompt 用文字要求格式 +
# 事後 regex 從 ```json 區塊挖」的作法。
GRAPH_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "industry": {"type": "string"},
                },
                "required": ["id", "industry"],
            },
        },
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "relation_type": {
                        "type": "string",
                        "enum": ["Supply", "Co-impact", "Substitution"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["source", "target", "relation_type", "reason"],
            },
        },
    },
    "required": ["nodes", "links"],
}


def extract_graph_from_summary(podcast_summary: str) -> str | None:
    prompt = """
    Role: 你是一位產業鏈分析專家，擅長從文本中萃取企業與產業間的「價值網圖譜」。

    Task: 請閱讀我提供的「財經 Podcast 摘要」資料，剖析出「公司與公司」或「公司與產業」之間的直接關聯。

    【節點（Node）規則】
    ✅ 合格的節點：
      - 具體公司名稱（如 台積電、Apple、鴻海）
      - 股票代號（如 NVDA、0050）
      - ETF 名稱
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
    半導體,
    電子/硬體,
    AI/軟體/雲端,
    金融,
    ETF/基金,
    能源,
    汽車/電動車,
    生技/醫療,
    消費/零售,
    工業/製造,
    媒體/娛樂,
    航太/國防,
    房地產/建設,
    電信/網通,
    航運/物流,
    若以上皆不適合，填寫為:其他。

    輸出內容規則（格式本身已由 response_schema 強制，不需要再自己組 JSON 外殼）：
    1. `nodes`：每個 node 的 id（公司或產業名稱）以及 industry 屬性。
    2. `links`：每條 link 的 source, target, reason, relation_type 屬性。
       ❌ source 與 target 絕對不能是同一個節點。
       ❌ reason 不能是「XXX屬於某產業」、「XXX隸屬於某行業」這類分類描述，必須是具體商業行為或產業關係。
    """

    print("\n🧠 [階段一] 呼叫 LLM 萃取 JSON 結構資料...")
    request_content = f"{prompt}\n\n【Podcast 摘要內容】：\n{podcast_summary}"

    output_text = _call_gemini_with_retry(request_content, response_schema=GRAPH_EXTRACTION_SCHEMA)

    # response_schema 已強制輸出是合法 JSON，這裡直接解析；
    # 保留 ```json 區塊的 fallback 只是防呆，理論上不會再走到。
    try:
        json.loads(output_text)
        print("✅ [階段一] 成功萃取 JSON。")
        return output_text
    except Exception:
        pass

    json_match = re.search(r'```json\n(.*?)\n```', output_text, re.DOTALL | re.IGNORECASE)
    if json_match:
        print("✅ [階段一] 成功萃取 JSON（fallback：從 ```json 區塊取出）。")
        return json_match.group(1)

    print("❌ 原始輸出非有效 JSON：")
    print(output_text)
    return None


# ==========================================
# 模組 3: 重複判斷
# ==========================================
def _normalize_node_name(name: str) -> str:
    """
    決定性正規化（不呼叫 LLM）：全形轉半形、去除空白、轉小寫。
    用來在丟給 LLM 語意比對之前，先攔掉單純因大小寫/全半形/空白
    造成的重複節點（例如 NVIDIA vs Nvidia、ＡＩ運算 vs AI運算）。
    """
    if not name:
        return ""
    normalized = unicodedata.normalize("NFKC", name)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.lower()


def _build_norm_index(names: list) -> dict:
    """正規化名稱 -> 原始正規節點名稱 的對照表，供 O(1) 直接命中。"""
    return {_normalize_node_name(n): n for n in names}


def _resolve_node_name(llm_name: str, existing_names: list, norm_index: dict) -> str:
    """
    決定 llm_name 該對應到哪個既有節點名稱。

    1. 先用決定性正規化比對 norm_index，命中就直接回傳，不呼叫 LLM。
    2. 沒命中才退回 LLM 語意判斷（處理「同一實體但用詞完全不同」的情況，
       例如中英文別名、簡稱），若有匹配，回傳 existing_names 中的正規名稱；
       否則回傳 llm_name 本身（代表全新節點）。
    每呼叫一次會動態比對當下的 existing_names（含本批次已新增的節點）。
    """
    if not existing_names:
        return llm_name

    norm_key = _normalize_node_name(llm_name)
    if norm_key in norm_index:
        return norm_index[norm_key]

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
        result = _call_gemini_with_retry(prompt).strip()
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
    try:
        return _call_gemini_with_retry(prompt).strip().upper().startswith("YES")
    except Exception as e:
        print(f"⚠️ reason 比對失敗，視為不重複。錯誤：{e}")
        return False


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
        # norm_index 讓大小寫/全半形/空白造成的重複可以直接命中，不必問 LLM
        norm_index: dict = _build_norm_index(existing_names)

        # ── 階段一：建立 llm名稱 → 正規名稱 對照表，並插入真正新的 nodes ──
        name_map: dict = {}

        for node in data.get("nodes", []):
            llm_name = node.get("id") or node.get("name")
            industry = node.get("industry")
            if not llm_name or not industry:
                continue

            canonical = _resolve_node_name(llm_name, existing_names, norm_index)
            name_map[llm_name] = canonical

            if canonical not in existing_nodes:
                cursor.execute(
                    "INSERT INTO nodes (name, industry) VALUES (%s, %s)",
                    [canonical, industry],
                )
                existing_nodes[canonical] = industry
                existing_names.append(canonical)
                norm_index[_normalize_node_name(canonical)] = canonical
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

    # 查詢已處理過的 podcast_source，跳過重複處理
    with connections["knowledge_graphdb"].cursor() as cursor:
        cursor.execute("SELECT DISTINCT podcast_source FROM links")
        processed_sources = {row[0] for row in cursor.fetchall()}
    print(f"⏭️  已處理過：{len(processed_sources)} 筆")

    skipped = 0
    for i, record in enumerate(records, 1):
        podcast_source = record.source_filename or f"record-{record.id}"

        if podcast_source in processed_sources:
            skipped += 1
            continue

        print(f"\n[{i}/{total}] 處理摘要 #{record.id}: {record.source_filename or '(無檔名)'}")
        try:
            summary_text = build_summary_text(record)
            summary_date = (record.published_at or record.created_at).date().isoformat()

            extracted_json = extract_graph_from_summary(summary_text)
            if extracted_json:
                save_to_db(
                    json_data_str=extracted_json,
                    summary_date=summary_date,
                    podcast_source=podcast_source,
                )
        except Exception as e:
            print(f"❌ [{i}/{total}] 處理失敗，跳過此筆。錯誤：{e}")

    print(f"\n✅ 完成！略過已處理 {skipped} 筆，共處理 {total - skipped} 筆。")


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