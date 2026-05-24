"""
apps/knowledge_graph/generate_narrative.py

敘事推理圖譜生成器（方向 B）

核心理念：
    原有 generate.py 從「壓縮後的摘要結構」萃取產業鏈關係（誰供應誰、誰競爭誰）。
    本模組從「Podcast 逐字稿的推理上下文」萃取因果推理鏈（為什麼這個判斷成立），
    節點範圍擴大到總經因素、市場趨勢、政策事件，關係類型改為因果語意。

輸入來源：PodcastTranscript.srt_content（逐字稿原文）
節點範圍：公司/產業/總經因素/市場趨勢/政策事件/市場情緒
關係類型：Causal / Drives / Suppresses / Benefits / Risk
儲存位置：knowledge_graphdb 的 nodes / links 表（同現有架構，relation_type 不同）

設計原則：
    - 完全不修改 generate.py
    - 可與原有圖譜資料共存於同一資料庫
    - 每個 argument（投資論點）對應一次子圖萃取
"""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from django.conf import settings
from django.db import connections
from google import genai

from apps.summaries.models import SummaryRecord
from apps.podcasts.models import PodcastEpisode, PodcastTranscript
from apps.summaries.services.srt import parse_srt, SrtItem


# ──────────────────────────────────────────────
# Gemini Client
# ──────────────────────────────────────────────

def _get_client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=settings.VERTEX_PROJECT_ID,
        location=settings.VERTEX_LOCATION,
    )


# ──────────────────────────────────────────────
# 模組 1：逐字稿解析與關鍵詞比對
# ──────────────────────────────────────────────

def _extract_keywords(text: str, min_len: int = 2) -> list[str]:
    """
    從文字中抽出長度 >= min_len 的有效詞彙。
    切分依據：中文標點、空格、括號。
    """
    parts = re.split(r'[\s，,。.、；;？?！!【】「」『』（）()：:\-—]+', text or "")
    return [p.strip() for p in parts if len(p.strip()) >= min_len]


def _score_srt_item(item: SrtItem, keywords: list[str]) -> int:
    """計算單筆 SRT item 包含多少個關鍵詞。"""
    return sum(1 for kw in keywords if kw in item.text)


def find_relevant_segments(
    srt_items: list[SrtItem],
    argument: dict,
    window_before: int = 10,
    window_after: int = 3,
    top_k: int = 2,
) -> list[dict]:
    """
    根據 argument 的 topic + summary 關鍵詞，
    在逐字稿中找出最相關的位置，並回傳包含前後文的上下文片段。

    Returns:
        list of {
            "anchor_idx": int,
            "context_text": str,       # 用於餵給 LLM 的上下文文字
            "anchor_text": str,        # 命中句
        }
    """
    topic_kws   = _extract_keywords(argument.get("topic", ""))
    summary_kws = _extract_keywords(argument.get("summary", ""))[:8]  # 只取前8個詞，避免雜訊
    keywords = list(dict.fromkeys(topic_kws + summary_kws))           # 去重，保序

    if not keywords or not srt_items:
        return []

    # 逐項打分
    scores = [_score_srt_item(item, keywords) for item in srt_items]

    # 取前 top_k 個最高分的位置（且分數 > 0）
    indexed = sorted(
        ((score, idx) for idx, score in enumerate(scores) if score > 0),
        reverse=True,
    )[:top_k]

    results = []
    seen_ranges: list[tuple[int, int]] = []  # 避免重疊

    for _, anchor_idx in indexed:
        start = max(0, anchor_idx - window_before)
        end   = min(len(srt_items) - 1, anchor_idx + window_after)

        # 跳過與已採用片段重疊超過 50% 的候選
        overlap = any(
            max(start, s) < min(end, e)
            and (min(end, e) - max(start, s)) > (end - start) * 0.5
            for s, e in seen_ranges
        )
        if overlap:
            continue

        seen_ranges.append((start, end))
        context_sentences = [srt_items[i].text for i in range(start, end + 1)]
        results.append({
            "anchor_idx":   anchor_idx,
            "anchor_text":  srt_items[anchor_idx].text,
            "context_text": "\n".join(context_sentences),
        })

    return results


# ──────────────────────────────────────────────
# 模組 2：LLM 萃取敘事推理圖譜
# ──────────────────────────────────────────────

_NARRATIVE_PROMPT_TEMPLATE = """
Role: 你是一位財經分析師，專門從 Podcast 對話中梳理「投資推理鏈」。

Background: 主持人正在討論以下投資論點：
【主題】{topic}
【立場】{position}
【論點摘要】{summary}

Task: 閱讀下方逐字稿片段，萃取主持人構建此論點時所依賴的因果推理結構。
重點是「為什麼」這個判斷成立，而非「誰跟誰有商業合作」。

【節點（Node）規則】
✅ 可以作為節點：
  - 公司或股票（台積電、NVIDIA、Apple）
  - 產業別（半導體、雲端服務、電動車）
  - 總體因素（升息、降息、通膨、美元走強、景氣衰退）
  - 市場趨勢（AI需求成長、算力需求、資金行情、去庫存）
  - 政策或事件（Fed降息、關稅政策、ESG規範、財報季）
  - 市場情緒（恐慌情緒、市場過熱、獲利了結潮）
❌ 不適合作為節點：
  - 具體人物姓名（黃仁勳、葛林斯潘）
  - 過於籠統的概念（「好公司」、「長期投資」、「風險」）
節點 id 要簡短精準（2-8字以內）。

【關係類型】只能從以下 5 種選一：
1. Causal     : A 直接導致 B 發生（例：升息 → Causal → 科技股下跌）
2. Drives     : A 驅動 B 的需求或成長（例：AI需求成長 → Drives → GPU出貨量）
3. Suppresses : A 對 B 產生負面抑制（例：高利率 → Suppresses → 成長股估值）
4. Benefits   : B 因 A 而正向受益（例：AI資料中心建設 → Benefits → 台積電）
5. Risk       : A 對 B 帶來風險暴露（例：地緣政治 → Risk → 半導體供應鏈）

【每個節點必須填寫 category】選擇最接近的一種：
公司、產業、總經、趨勢、政策、情緒

【reason 規則】
  - 必須用一句話（15-50字）說明「為什麼 A 對 B 有此影響」
  - 必須是逐字稿中能找到依據的內容，不能自行腦補

Output Format: 只輸出 JSON，用 ```json 包裝。不要輸出任何解釋。
{{
  "nodes": [{{"id": "...", "category": "..."}}],
  "links": [{{"source": "...", "target": "...", "relation_type": "...", "reason": "..."}}]
}}

【逐字稿片段】
{context}
"""


def extract_narrative_graph(
    context_text: str,
    argument: dict,
) -> Optional[str]:
    """
    將逐字稿上下文 + 投資論點提示送給 Gemini，
    萃取敘事推理圖譜 JSON。
    回傳 JSON 字串，失敗回傳 None。
    """
    prompt = _NARRATIVE_PROMPT_TEMPLATE.format(
        topic=argument.get("topic", ""),
        position=argument.get("position", "中性"),
        summary=argument.get("summary", ""),
        context=context_text,
    )

    try:
        client = _get_client()
        model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")
        response = client.models.generate_content(model=model_name, contents=prompt)
        text = response.text

        m = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1)

        # 嘗試直接解析
        json.loads(text)
        return text

    except Exception as e:
        print(f"  ⚠️ [敘事萃取] LLM 失敗：{e}")
        return None


# ──────────────────────────────────────────────
# 模組 3：寫入資料庫
# ──────────────────────────────────────────────

# category → industry 的對應表
_CATEGORY_TO_INDUSTRY = {
    "公司":  None,        # 保留原 category，讓 id 隱含產業
    "產業":  None,        # 同上
    "總經":  "總體經濟",
    "趨勢":  "市場趨勢",
    "政策":  "政策事件",
    "情緒":  "市場情緒",
}


def _category_to_industry(category: str, node_id: str) -> str:
    mapped = _CATEGORY_TO_INDUSTRY.get(category)
    if mapped:
        return mapped
    # 公司/產業 → 用 node_id 本身作為 industry（後續可被覆蓋）
    return category or "未分類"


def _narrative_link_exists(cursor, source: str, target: str, relation_type: str) -> bool:
    """
    30 天內是否已有相同 source + target + relation_type 的敘事連線。
    敘事圖用更寬鬆的精確比對（不呼叫 LLM）以節省 API 次數。
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date()
    cursor.execute(
        "SELECT 1 FROM links "
        "WHERE source = %s AND target = %s AND relation_type = %s AND summary_date >= %s "
        "LIMIT 1",
        [source, target, relation_type, cutoff],
    )
    return cursor.fetchone() is not None


def save_narrative_to_db(
    json_str: str,
    summary_date: str,
    podcast_source: str,
) -> dict:
    """
    將萃取的敘事圖譜 JSON 存入 knowledge_graphdb。
    回傳 {"nodes_inserted": int, "links_inserted": int}。
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"  ❌ JSON 解析失敗：{e}")
        return {"nodes_inserted": 0, "links_inserted": 0}

    nodes_inserted = 0
    links_inserted = 0

    with connections["knowledge_graphdb"].cursor() as cursor:

        # 載入現有節點（正規化名稱基準）
        cursor.execute("SELECT name FROM nodes")
        existing_names: set[str] = {row[0] for row in cursor.fetchall()}

        # name_map：LLM 輸出的 id → 實際存入的名稱（去重用）
        name_map: dict[str, str] = {}

        for node in data.get("nodes", []):
            node_id  = (node.get("id") or "").strip()
            category = (node.get("category") or "").strip()
            if not node_id:
                continue

            industry = _category_to_industry(category, node_id)

            # 簡單去重：名稱完全相同才視為已存在（敘事節點名稱通常較為固定）
            if node_id in existing_names:
                name_map[node_id] = node_id
            else:
                cursor.execute(
                    "INSERT INTO nodes (name, industry) VALUES (%s, %s)",
                    [node_id, industry],
                )
                existing_names.add(node_id)
                name_map[node_id] = node_id
                nodes_inserted += 1
                print(f"    ✅ 新增節點：{node_id}（{industry}）")

        # 插入 links
        for link in data.get("links", []):
            src = name_map.get(link.get("source", ""), link.get("source", ""))
            tgt = name_map.get(link.get("target", ""), link.get("target", ""))
            rel = link.get("relation_type", "")
            reason = link.get("reason", "")

            if not src or not tgt or not rel:
                continue
            if src == tgt:
                continue
            if src not in existing_names or tgt not in existing_names:
                print(f"    ⚠️ 跳過（節點不存在）：{src} → {tgt}")
                continue

            if _narrative_link_exists(cursor, src, tgt, rel):
                print(f"    ⏭️ 連線已存在，跳過：{src} →[{rel}]→ {tgt}")
            else:
                cursor.execute(
                    """
                    INSERT INTO links (source, target, relation_type, reason, summary_date, podcast_source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [src, tgt, rel, reason, summary_date, podcast_source],
                )
                links_inserted += 1
                print(f"    ✅ 新增連線：{src} →[{rel}]→ {tgt}")

    return {"nodes_inserted": nodes_inserted, "links_inserted": links_inserted}


# ──────────────────────────────────────────────
# 模組 4：SummaryRecord ↔ PodcastTranscript 配對
# ──────────────────────────────────────────────

def _find_transcript(record: SummaryRecord) -> Optional[str]:
    """
    根據 SummaryRecord 的 source_filename 或 published_at，
    嘗試找出對應的 PodcastTranscript.srt_content。

    匹配策略（依優先順序）：
    1. episode_title 包含 source_filename（或反之）
    2. 若 record.published_at 有值，匹配日期
    回傳 srt_content 字串，找不到回傳 None。
    """
    source = (record.source_filename or "").strip()
    if not source:
        return None

    try:
        # 策略 1：標題包含關鍵字
        episodes = PodcastEpisode.objects.using("podcasts").filter(
            episode_title__icontains=source
        ).select_related("transcript")[:1]

        if not episodes:
            # 反向：source_filename 包含 episode_title 的某部分
            # 取前 20 字作為搜尋依據
            short_source = source[:20]
            episodes = PodcastEpisode.objects.using("podcasts").filter(
                episode_title__icontains=short_source
            ).select_related("transcript")[:1]

        if episodes:
            ep = episodes[0]
            if hasattr(ep, "transcript") and ep.transcript:
                return ep.transcript.srt_content

        # 策略 2：日期匹配
        if record.published_at:
            date_str = record.published_at.date()
            episodes_by_date = PodcastEpisode.objects.using("podcasts").filter(
                published_at__date=date_str
            ).select_related("transcript")[:1]
            if episodes_by_date:
                ep = episodes_by_date[0]
                if hasattr(ep, "transcript") and ep.transcript:
                    return ep.transcript.srt_content

    except Exception as e:
        print(f"  ⚠️ 查詢逐字稿失敗：{e}")

    return None


# ──────────────────────────────────────────────
# 模組 5：Narrative Support Score（NSS）
# ──────────────────────────────────────────────

def compute_nss(graph_json: dict) -> float:
    """
    計算一次敘事圖萃取結果的 Narrative Support Score。

    NSS = 節點數 + 連線數 + 正向關係比例加成 - 孤立節點懲罰

    正向關係（Benefits, Drives）代表支撐性敘事，權重較高。
    Risk / Suppresses 代表風險提示，中性計分。
    孤立節點（degree = 0）扣分，代表敘事支撐薄弱。

    回傳 0.0 ~ 1.0 的正規化分數（分母固定為 20，超過視為滿分）。
    """
    nodes = graph_json.get("nodes", [])
    links = graph_json.get("links", [])

    if not nodes and not links:
        return 0.0

    node_count = len(nodes)
    link_count = len(links)

    # 正向關係加成
    positive_types = {"Benefits", "Drives"}
    positive_count = sum(1 for l in links if l.get("relation_type") in positive_types)
    positive_bonus = positive_count * 0.5

    # 孤立節點懲罰（degree = 0）
    node_ids = {n.get("id") for n in nodes}
    connected = set()
    for l in links:
        connected.add(l.get("source"))
        connected.add(l.get("target"))
    isolated = len(node_ids - connected)
    isolation_penalty = isolated * 0.3

    raw_score = node_count + link_count + positive_bonus - isolation_penalty
    return round(min(raw_score / 20.0, 1.0), 3)


# ──────────────────────────────────────────────
# 模組 6：單筆 Pipeline
# ──────────────────────────────────────────────

def process_summary_with_transcript(
    record: SummaryRecord,
    srt_content: str,
    summary_date: str,
    podcast_source: str,
) -> list[dict]:
    """
    對一筆 SummaryRecord + 對應逐字稿，依每個 argument 萃取敘事圖。

    流程：
    1. 解析逐字稿 SRT → SrtItem 列表
    2. 對每個 argument：找相關逐字稿片段 → 呼叫 LLM → 存入 DB
    3. 回傳每個 argument 的處理結果（含 NSS 分數）

    Returns:
        list of {
            "topic": str,
            "nss": float,
            "nodes_inserted": int,
            "links_inserted": int,
            "skipped": bool,  # True 表示找不到對應片段
        }
    """
    srt_items = parse_srt(srt_content)
    if not srt_items:
        print("  ⚠️ 逐字稿解析為空，跳過")
        return []

    arguments = record.arguments or []
    if not arguments:
        print("  ⚠️ 無 arguments，跳過")
        return []

    results = []

    for arg in arguments:
        topic = arg.get("topic", "(無主題)")
        print(f"\n  📌 處理論點：{topic}")

        segments = find_relevant_segments(srt_items, arg)

        if not segments:
            print(f"    ⚠️ 找不到相關逐字稿片段，跳過")
            results.append({"topic": topic, "nss": 0.0, "nodes_inserted": 0,
                            "links_inserted": 0, "skipped": True})
            continue

        total_nodes = 0
        total_links = 0
        best_nss = 0.0

        for seg in segments:
            print(f"    🔍 錨定句（idx={seg['anchor_idx']}）：{seg['anchor_text'][:40]}…")
            json_str = extract_narrative_graph(seg["context_text"], arg)
            if not json_str:
                continue

            try:
                graph_json = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            nss = compute_nss(graph_json)
            best_nss = max(best_nss, nss)
            print(f"    📊 NSS = {nss}")

            counts = save_narrative_to_db(json_str, summary_date, podcast_source)
            total_nodes += counts["nodes_inserted"]
            total_links += counts["links_inserted"]

        results.append({
            "topic":           topic,
            "nss":             best_nss,
            "nodes_inserted":  total_nodes,
            "links_inserted":  total_links,
            "skipped":         False,
        })

    return results


# ──────────────────────────────────────────────
# 模組 7：批次 Pipeline（自動配對逐字稿）
# ──────────────────────────────────────────────

def process_one_narrative(summary_id: int) -> list[dict]:
    """
    處理單一 SummaryRecord（透過 ID 指定）。
    自動嘗試從 podcasts DB 找對應的逐字稿。
    """
    try:
        record = SummaryRecord.objects.using("summariesdb").get(pk=summary_id)
    except SummaryRecord.DoesNotExist:
        print(f"❌ SummaryRecord #{summary_id} 不存在")
        return []

    srt_content = _find_transcript(record)
    if not srt_content:
        print(f"❌ 找不到對應逐字稿：{record.source_filename}")
        return []

    summary_date = (record.published_at or record.created_at).date().isoformat()
    podcast_source = record.source_filename or f"record-{record.id}"

    print(f"\n🚀 [敘事圖] 處理摘要 #{record.id}：{podcast_source} ({summary_date})")
    return process_summary_with_transcript(record, srt_content, summary_date, podcast_source)


def process_all_narrative(limit: Optional[int] = None) -> dict:
    """
    批次處理所有 SummaryRecord，依序嘗試配對逐字稿並萃取敘事圖。

    Args:
        limit: 最多處理幾筆（None = 全部）

    Returns:
        {
            "total_records": int,
            "processed": int,
            "skipped_no_transcript": int,
            "total_nodes_inserted": int,
            "total_links_inserted": int,
        }
    """
    qs = SummaryRecord.objects.using("summariesdb").defer(
        "glossary_matches", "mind_map"
    ).order_by("created_at")

    if limit:
        qs = qs[:limit]

    total = qs.count()
    print(f"\n📚 共 {total} 筆摘要待處理（敘事圖模式）")

    stats = {
        "total_records":           total,
        "processed":               0,
        "skipped_no_transcript":   0,
        "total_nodes_inserted":    0,
        "total_links_inserted":    0,
    }

    for i, record in enumerate(qs, 1):
        print(f"\n[{i}/{total}] 摘要 #{record.id}：{record.source_filename or '(無檔名)'}")

        srt_content = _find_transcript(record)
        if not srt_content:
            print("  ⏭️ 無對應逐字稿，跳過")
            stats["skipped_no_transcript"] += 1
            continue

        summary_date   = (record.published_at or record.created_at).date().isoformat()
        podcast_source = record.source_filename or f"record-{record.id}"

        results = process_summary_with_transcript(
            record, srt_content, summary_date, podcast_source
        )

        stats["processed"] += 1
        for r in results:
            stats["total_nodes_inserted"] += r.get("nodes_inserted", 0)
            stats["total_links_inserted"] += r.get("links_inserted", 0)

    print(f"\n✅ 批次完成：{stats}")
    return stats


# ──────────────────────────────────────────────
# 模組 8：投資主張推理鏈萃取
# 以 SummaryRecord.arguments 作為錨點，針對每個論點
# 用 find_relevant_segments 定位相關逐字稿段落，
# 再由 LLM 萃取 source→target 因果鏈（不存 DB）
# ──────────────────────────────────────────────

_CLAIM_CHAIN_PROMPT = """\
Role: 你是一位財經研究員，從 Podcast 逐字稿中還原主持人的投資推理過程。

背景 — 主持人正在討論的投資論點：
  【主題】{topic}
  【立場】{position}
  【論點摘要】{summary}

Task: 根據下方逐字稿片段，萃取主持人推導此論點的因果推理鏈。

規則：
  - chain 中每一步是「一個概念節點」到「下一個概念節點」的推理跳躍
  - source / target 用簡短詞彙（2-8 字），代表公司、產業、總經因素、政策、市場趨勢等
  - label 用一句話（15-35 字）說明「主持人為何認為 source 影響 target」，必須有逐字稿依據
  - 整條鏈通常 2-5 步，最後的 target 通常是主持人的結論（股票/操作建議）
  - 不需要定義關係類型，只需要 source、target、label 三個欄位

Output Format: 只輸出 JSON，用 ```json 包裝，不要任何解釋。
{{
  "chain": [
    {{"source": "起點概念", "target": "終點概念", "label": "說明為何影響的一句話"}},
    ...
  ],
  "investment_implication": "對投資人最直接的操作意涵（20-35 字）"
}}

【逐字稿片段】
{context}
"""


def extract_claim_chain(context: str, argument: dict) -> Optional[dict]:
    """
    針對單一 argument，用相關逐字稿片段呼叫 LLM，
    萃取 source→target 因果推理鏈。
    回傳 {"chain": [...], "investment_implication": "..."}，失敗回傳 None。
    """
    prompt = _CLAIM_CHAIN_PROMPT.format(
        topic=argument.get("topic", ""),
        position=argument.get("position", "中性"),
        summary=argument.get("summary", ""),
        context=context,
    )

    try:
        client = _get_client()
        model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")
        response = client.models.generate_content(model=model_name, contents=prompt)
        text = response.text

        m = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
        raw = m.group(1) if m else text
        return json.loads(raw)

    except Exception as e:
        print(f"  ⚠️ [claim_chain] LLM 失敗：{e}")
        return None


# ──────────────────────────────────────────────
# 模組 9：清單 + 主入口
# ──────────────────────────────────────────────

def list_summaries_with_arguments(limit: int = 100) -> list[dict]:
    """
    回傳有 arguments 資料的 SummaryRecord 清單（最新優先），供前端選集。
    """
    records = (
        SummaryRecord.objects
        .using("summariesdb")
        .exclude(arguments=[])
        .exclude(arguments__isnull=True)
        .order_by("-published_at")
        .values("id", "source_filename", "podcaster", "published_at", "arguments")
        [:limit]
    )
    result = []
    for r in records:
        arg_count = len(r["arguments"]) if isinstance(r["arguments"], list) else 0
        result.append({
            "id":              r["id"],
            "source_filename": r["source_filename"] or f"摘要 #{r['id']}",
            "podcaster":       r["podcaster"] or "",
            "published_at":    r["published_at"].date().isoformat() if r["published_at"] else "",
            "arg_count":       arg_count,
        })
    return result


def analyze_from_summary(summary_id: int) -> dict:
    """
    主入口：根據 SummaryRecord.arguments 逐一萃取推理鏈。

    流程：
      1. 取 SummaryRecord → arguments 清單
      2. _find_transcript() 找對應逐字稿
      3. parse_srt() 解析成 SrtItem 列表
      4. 對每個 argument：
         a. find_relevant_segments() 用關鍵詞定位相關段落
         b. 沒找到則用 argument.summary 作為 fallback context
         c. extract_claim_chain() 萃取 source→target 鏈
      5. 回傳所有 claim（不存 DB）

    回傳：
      {
        "record": { id, source_filename, published_at, podcaster },
        "claims": [
          {
            "topic": str,
            "position": str,
            "summary": str,       # 論點摘要，作為卡片標題
            "chain": [...],       # [{source, target, label}]
            "investment_implication": str,
          }
        ],
        "error": str | None,
      }
    """
    try:
        record = SummaryRecord.objects.using("summariesdb").get(pk=summary_id)
    except SummaryRecord.DoesNotExist:
        return {"record": None, "claims": [], "error": f"摘要 #{summary_id} 不存在"}

    record_info = {
        "id":              record.id,
        "source_filename": record.source_filename or f"摘要 #{record.id}",
        "published_at":    record.published_at.date().isoformat() if record.published_at else "",
        "podcaster":       record.podcaster or "",
    }

    arguments = record.arguments or []
    if not arguments:
        return {"record": record_info, "claims": [], "error": "此摘要沒有 arguments 資料"}

    # ── 找逐字稿 ──
    srt_content = _find_transcript(record)
    srt_items: list = []
    if srt_content:
        srt_items = parse_srt(srt_content)
        print(f"📄 逐字稿：{len(srt_items)} 段")
    else:
        print("⚠️  找不到逐字稿，將以論點摘要作為 context fallback")

    print(f"\n🚀 [claim_chain] 摘要 #{record.id}：{record_info['source_filename']}")
    print(f"   共 {len(arguments)} 個論點待萃取\n")

    claims = []
    for arg in arguments:
        topic   = arg.get("topic", "(無主題)")
        summary = arg.get("summary", "")
        print(f"  📌 [{topic}]")

        # ── 定位相關逐字稿段落 ──
        if srt_items:
            segments = find_relevant_segments(srt_items, arg)
            if segments:
                context = "\n\n".join(seg["context_text"] for seg in segments)
                print(f"     ✅ 找到 {len(segments)} 個相關片段")
            else:
                context = summary  # fallback
                print("     ⚠️  無相關片段，用論點摘要作 fallback")
        else:
            context = summary

        if not context.strip():
            print("     ⏭️  context 為空，跳過")
            continue

        result = extract_claim_chain(context, arg)
        if not result:
            print("     ❌ LLM 萃取失敗，跳過")
            continue

        chain = result.get("chain", [])
        print(f"     📊 推理鏈：{len(chain)} 步")
        for step in chain:
            print(f"        {step.get('source','')} → {step.get('target','')}：{step.get('label','')[:30]}…")

        claims.append({
            "topic":                  topic,
            "position":               arg.get("position", "中性"),
            "summary":                summary,
            "chain":                  chain,
            "investment_implication": result.get("investment_implication", ""),
        })

    print(f"\n✅ 完成，共萃取 {len(claims)} 條推理鏈")
    return {"record": record_info, "claims": claims, "error": None}


def test_one_episode(summary_id: int = None) -> dict:
    """
    測試用：從 summariesdb 取一筆摘要，跑完整推理鏈萃取流程。
    summary_id=None 時自動取最新有 arguments 的摘要。

    Django shell 使用方式：
        from apps.knowledge_graph.generate_narrative import test_one_episode
        result = test_one_episode()     # 自動取最新
        result = test_one_episode(42)   # 指定摘要 ID
    """
    if summary_id is None:
        record = (
            SummaryRecord.objects
            .using("summariesdb")
            .exclude(arguments=[])
            .order_by("-published_at")
            .first()
        )
        if not record:
            print("❌ 找不到任何有 arguments 的摘要")
            return {"record": None, "claims": [], "error": "無資料"}
        summary_id = record.id

    return analyze_from_summary(summary_id)
