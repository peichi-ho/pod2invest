# apps/ai_assistant/management/commands/eval_rag.py
"""
RAG Evaluation Pipeline
=======================
Step 1  生成 eval 題目（A / B / C 題型）
Step 2  Retrieval（取 top-5 chunks）
Step 3.1  Precision@5
Step 3.2  Coverage（retrieval 覆蓋面向）
Step 4  生成 RAG 答案
Step 5  評估答案品質（Coverage / Faithfulness / Relevance / Comparison Quality）

使用方式：
    python manage.py eval_rag
    python manage.py eval_rag --per-type 5
    python manage.py eval_rag --per-type 3 --seed 42
"""

import json
import logging
import random
import time

from django.core.management.base import BaseCommand
from django.db import connections

logger = logging.getLogger(__name__)

# 各題型的 retrieval 設定
RETRIEVAL_CFG_BY_TYPE = {
    "A": {"top_k": 5,  "n_candidates": 30, "max_per_episode": None},
    "B": {"top_k": 10, "n_candidates": 30, "max_per_episode": 3},
    "C": {"top_k": 10, "n_candidates": 30, "max_per_episode": 3},
}

# chunk 抽樣設定：每種題型用多少 record 和每 record 幾個 chunk
_SAMPLE_CFG = {
    "A": {"n_records": 1, "chunks_per_record": 4},
    "B": {"n_records": 3, "chunks_per_record": 2},
    "C": {"n_records": 2, "chunks_per_record": 3},
}

# ===========================================================================
# Prompts（原文保留，不修改）
# ===========================================================================

STEP1_PROMPT = """\
你是一個 Podcast 內容分析助手，負責為 RAG 系統建立評估題目。

請根據提供的 podcast 內容，生成一個符合指定題型的使用者問題，並定義「完整回答該問題應涵蓋的關鍵面向」。

【題型】
{question_type}

題型說明：
- A（single）：答案主要來自單一集數或少數相鄰 chunk，重點在精準資訊
- B（multi）：答案需要整合多集 podcast 的內容，重點在多來源整理
- C（comparison）：答案需要比較不同集數 / 來賓 / 時間點的觀點或變化

---

【輸入資料】
{chunks}

---

【任務 1：生成問題】

請根據指定題型生成問題，並遵守以下規則：

### 若題型為 A（single）：
1. 問題應可由單一集數或少數 chunk 回答
2. 問題聚焦於具體觀點、解釋或細節
3. 不需要跨集整合

---

### 若題型為 B（multi）：
1. 問題應需要多個 chunk / 多集資訊共同回答
2. 問題為主題型、整理型問題
3. 避免單一 chunk 就能完整回答

---

### 若題型為 C（comparison）：
1. 問題必須涉及「比較」或「變化」
2. 必須涉及不同集數 / 來賓 / 時間點
3. 問題不能只是列舉，而是要求差異或趨勢

---

【任務 2：生成 must_cover_points】

請定義「完整回答該問題時，應涵蓋的 3~6 個關鍵面向」。

要求：
1. 每個面向是「資訊類別」，不是句子
2. 面向之間不可重複
3. 面向要可用於評估（可以判斷是否被涵蓋）
4. 面向必須來自提供的內容

---

【額外要求（重要）】

- 問題不可直接引用原文句子
- 問題需自然、像使用者會問的問題
- must_cover_points 應與問題一致，不可偏離

---

【輸出格式】

請輸出 JSON：

{
  "question": "...",
  "must_cover_points": [
    "...",
    "...",
    "..."
  ],
  "question_type": "{question_type}",
  "source_chunk_ids": [],
  "source_episode_ids":[]
}\
"""

STEP3_1_PROMPT = """\
你是一個資訊檢索評估器。

請判斷以下每一個 chunk 是否「與問題直接相關」，並且能幫助回答問題。

問題：
{question}

Chunks：
1. {chunk_1}
2. {chunk_2}
3. {chunk_3}
4. {chunk_4}
5. {chunk_5}

判斷標準：

- relevant：
  該 chunk 包含「可用於回答問題的實質資訊」（例如觀點、解釋、數據、原因）

- partially_relevant：
  與主題相關，但資訊不完整或幫助有限

- irrelevant：
  與問題無關，或只是提到關鍵字但沒有提供可用資訊

---

請逐一判斷每個 chunk，並簡短說明原因。

請輸出 JSON：

{
  "results": [
    {
      "chunk_id": 1,
      "label": "relevant / partially_relevant / irrelevant",
      "reason": "..."
    }
  ],
  "precision_at_5": 0.0
}

precision_at_5 計算方式：
relevant = 1
partially_relevant = 0.5
irrelevant = 0
總和 / 5\
"""

STEP3_2_PROMPT = """\
你是一個 RAG 評估器。

請判斷以下 chunks 是否涵蓋回答問題所需的關鍵面向。

問題：
{question}

題型：
{question_type}

應涵蓋面向：
{must_cover_points}

Chunks：
{top_5_chunks}

---

請完成以下任務：

【任務 1：面向覆蓋】

逐一判斷每個面向是否有被涵蓋：

- covered：chunk 中有明確資訊
- partially_covered：有提到但不完整
- not_covered：完全沒有

---

【任務 2：來源多樣性（重要）】

若題型為 B（multi）或 C（comparison）：

請判斷這些 chunks 是否來自「不同集數或來源」，而不是集中在單一來源。

---

請輸出 JSON：

{
  "points": [
    {
      "point": "...",
      "status": "covered / partially_covered / not_covered"
    }
  ],
  "covered_points": [],
  "missing_points": [],
  "coverage_score": 0.0,
  "source_diversity": "high / medium / low",
  "notes": "..."
}

---

coverage_score 計算方式：

covered = 1
partially_covered = 0.5
not_covered = 0

總和 / 面向數量\
"""

STEP5_PROMPT = """\
你是一個 RAG 評估器，負責評估 AI 回答的品質。

請嚴格依據提供的 context 進行判斷，不可以使用外部知識或常識補充。

---

【問題】
{question}

【題型】
{question_type}
（A = 單集 / B = 跨集主題 / C = 比較）

【檢索到的內容（context）】
{retrieved_chunks}

【應涵蓋面向】
{must_cover_points}

【AI 回答】
{answer}

---

請完成以下評估：

====================
【任務 1：面向覆蓋（Coverage）】
====================

逐一判斷每個面向是否有被涵蓋：

- covered：回答中明確提到且有說明
- partially_covered：有提到但不完整或不清楚
- not_covered：完全沒有提到

請務必逐點判斷。

coverage_score 計算方式：
covered = 1
partially_covered = 0.5
not_covered = 0
總和 / 面向數量

---

====================
【任務 2：忠實性（Faithfulness）】
====================

判斷 AI 回答中的內容是否都有 context 支撐：

- faithful：所有重要內容都可在 context 中找到依據
- partially_faithful：大部分有依據，但有少量無法確認
- not_faithful：存在明顯未出現在 context 的資訊

請列出：

- unsupported_claims：回答中無法從 context 找到依據的內容（若沒有則為空陣列）

---

====================
【任務 3：是否回答問題（Relevance）】
====================

判斷回答是否有針對問題：

- yes：直接回答問題且重點正確
- partial：只回答部分或偏離重點
- no：沒有真正回答問題

請提供簡短理由。

---

====================
【任務 4：比較品質（Comparison Quality）】
（僅適用於 C 題）
====================

⚠️ 若題型不是 C（comparison），請將此欄位設為 null

若題型為 C，請評估：

1. 是否有明確比較不同來源（例如不同集數 / 來賓 / 時間點）
2. 是否指出差異或變化
3. 是否只是列舉而沒有比較

評分標準：

- good：有清楚比較且指出差異或變化
- weak：有提多來源但比較不明確
- none：沒有比較，只是列舉或單一觀點

並輸出：

- has_clear_comparison：true / false
- missing_comparisons：缺少的比較面向

---

請輸出 JSON（不要輸出其他文字）：

{
  "coverage": {
    "results": [
      {
        "point": "...",
        "status": "covered / partially_covered / not_covered"
      }
    ],
    "coverage_score": 0.0
  },
  "faithfulness": {
    "label": "faithful / partially_faithful / not_faithful",
    "unsupported_claims": []
  },
  "relevance": {
    "label": "yes / partial / no",
    "reason": "..."
  },
  "comparison_quality": {
    "label": "good / weak / none",
    "has_clear_comparison": true,
    "missing_comparisons": []
  }
}\
"""


ENTITY_EXTRACT_PROMPT = """\
從以下問題中，抽出明確提及的實體名稱，填入對應欄位。

規則：
- entity_companies：只填真實公司名或股票名稱（例：台積電、NVIDIA、聯發科、鴻海）。「美股」、「台股」、「科技巨頭」、「AI公司」等概念詞不算，填空陣列。
- entity_people：只填真實人名（例：黃仁勳、川普、鮑威爾）。職稱、角色名稱不算。
- entity_regions：只填真實國家或地區名稱（例：美國、台灣、中國、日本）。「美股」、「台股」是市場代稱，不算地區，填空陣列。
- 若問題沒有明確提及，該欄位填空陣列，不要猜測。

只回傳 JSON，格式如下：
{"entity_companies": [], "entity_people": [], "entity_regions": []}

問題：{question}
"""


# ===========================================================================
# Helpers
# ===========================================================================

def _fill(template: str, **kwargs) -> str:
    """變數替換，不影響 prompt 中的 JSON 範例 {}。"""
    result = template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def _call_json(client, model_name: str, prompt: str) -> dict:
    resp = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config={"response_mime_type": "application/json", "temperature": 0.0},
    )
    try:
        return json.loads(resp.text)
    except json.JSONDecodeError:
        logger.warning(f"JSON parse failed: {resp.text[:300]}")
        return {}


def _call_text(client, model_name: str, prompt: str) -> str:
    resp = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config={"temperature": 0.2},
    )
    return resp.text.strip()


def _format_chunks_for_prompt(chunks: list[dict]) -> str:
    """將 chunk list 格式化成 prompt 用的文字。
    來源標籤用 A/B/C 代替集數編號，避免 Gemini 在問題中引用不自然的 Episode ID。
    """
    labels: dict[int, str] = {}
    label_chars = "ABCDEFGHIJ"
    for c in chunks:
        rid = c["record_id"]
        if rid not in labels:
            labels[rid] = label_chars[len(labels)]

    parts = []
    for c in chunks:
        label = labels[c["record_id"]]
        parts.append(
            f"[來源 {label} | Type: {c['chunk_type']}]\n{c['chunk_text']}"
        )
    return "\n\n---\n\n".join(parts)


# ===========================================================================
# 抽樣
# ===========================================================================

def _sample_chunks(question_type: str) -> list[dict]:
    """依題型從 DB 抽取對應的 chunk 組合，回傳 chunks 以及實際的 actual_record_ids。"""
    cfg = _SAMPLE_CFG[question_type]
    n_records = cfg["n_records"]
    per_record = cfg["chunks_per_record"]

    # 取足夠多候選再 Python 端隨機選，確保不會重複
    with connections["ai_assistant_db"].cursor() as cur:
        cur.execute(
            "SELECT record_id FROM podcast_embedded_chunks GROUP BY record_id ORDER BY RANDOM() LIMIT %s",
            [n_records * 5],
        )
        candidates = [r[0] for r in cur.fetchall()]

    record_ids = list(dict.fromkeys(candidates))[:n_records]  # deduplicate, preserve order

    chunks = []
    for rid in record_ids:
        with connections["ai_assistant_db"].cursor() as cur:
            cur.execute(
                """
                SELECT id, record_id, chunk_type, chunk_text, source_filename
                FROM podcast_embedded_chunks
                WHERE record_id = %s
                ORDER BY RANDOM()
                LIMIT %s
                """,
                [rid, per_record],
            )
            for row in cur.fetchall():
                chunks.append({
                    "id": row[0], "record_id": row[1],
                    "chunk_type": row[2], "chunk_text": row[3],
                    "source_filename": row[4],
                })
    return chunks


STEP1_VALIDATION_PROMPT = """\
請判斷以下問題是否適合作為財經 RAG 系統的評估問題。

判斷標準：
1. 問題必須與財經、投資、股票、市場、產業、總體經濟等主題相關
2. 問題必須自然，像真實用戶會問的問題（不可引用「來源 A / B」、集數編號或內部標記）
3. 問題必須可根據 podcast 內容回答，而非依賴即時報價或外部資料
4. 不能是非財經生活類問題（如健康、育兒、人際關係、心理、旅遊等）

問題：{question}

只回傳 JSON：
{{"valid": true, "reason": "..."}}
"""


# ===========================================================================
# Step 1：生成 eval 題目
# ===========================================================================

def step1_generate_question(chunks: list[dict], question_type: str, client, model_name: str) -> dict:
    chunks_text = _format_chunks_for_prompt(chunks)
    prompt = _fill(STEP1_PROMPT, question_type=question_type, chunks=chunks_text)
    result = _call_json(client, model_name, prompt)
    return result


def step1_validate_question(question: str, client, model_name: str) -> dict:
    """驗證問題是否為有效的財經 RAG 評估問題。"""
    prompt = _fill(STEP1_VALIDATION_PROMPT, question=question)
    result = _call_json(client, model_name, prompt)
    return result


def generate_valid_question(qtype: str, client, model_name: str, max_retries: int = 3):
    """抽樣 → 生成問題 → 驗證，不通過就重試，最多 max_retries 次。
    回傳 (step1_result, source_chunks, actual_record_ids) 或 (None, None, None)。
    """
    for attempt in range(max_retries):
        source_chunks = _sample_chunks(qtype)
        actual_record_ids = list(dict.fromkeys(c["record_id"] for c in source_chunks))
        step1 = step1_generate_question(source_chunks, qtype, client, model_name)
        question = step1.get("question", "").strip()
        time.sleep(0.3)

        if not question:
            continue

        validation = step1_validate_question(question, client, model_name)
        time.sleep(0.3)

        if validation.get("valid"):
            return step1, source_chunks, actual_record_ids

        logger.info(
            f"  [validation fail attempt {attempt+1}/{max_retries}] "
            f"reason={validation.get('reason','')} | Q={question[:60]}"
        )

    return None, None, None


# ===========================================================================
# Step 2：Retrieval（與生產邏輯一致：Planner entity 抽取 + metadata filter + 向量搜尋）
# ===========================================================================

def _extract_filters(question: str, client, model_name: str) -> dict | None:
    """從問題抽取 entity，對應 Planner 的行為。"""
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=_fill(ENTITY_EXTRACT_PROMPT, question=question),
            config={"response_mime_type": "application/json", "temperature": 0.0},
        )
        data = json.loads(resp.text)
        filters = {}
        if data.get("entity_companies"):
            filters["entity_companies"] = data["entity_companies"]
        if data.get("entity_people"):
            filters["entity_people"] = data["entity_people"]
        if data.get("entity_regions"):
            filters["entity_regions"] = data["entity_regions"]
        return filters or None
    except Exception as e:
        logger.warning(f"entity extraction failed: {e}")
        return None


def step2_retrieve(
    question: str,
    embed_model,
    client,
    model_name: str,
    top_k: int = 5,
    n_candidates: int = 20,
    max_per_episode: int | None = None,
) -> dict:
    """
    生產搜尋邏輯（與 search_podcast_transcript 一致）：
      entity 抽取 → metadata filter → 向量搜尋（候選池）→ Hybrid BM25 重排 → diversity → top_k
    """
    from apps.ai_assistant.services.ai_assistant import _hybrid_rerank, _apply_diversity

    # entity 抽取（Planner 行為）
    filters = _extract_filters(question, client, model_name)
    time.sleep(0.2)

    # embed query
    vec = embed_model.encode(f"query: {question}", normalize_embeddings=True)
    vec_str = "[" + ",".join(f"{v:.8f}" for v in vec) + "]"

    # build WHERE
    where_parts: list[str] = []
    where_params: list = []
    if filters:
        entity_conds: list[str] = []
        for c in (filters.get("entity_companies") or []):
            entity_conds.append("entity_companies @> %s::jsonb")
            where_params.append(json.dumps([c], ensure_ascii=False))
        for p in (filters.get("entity_people") or []):
            entity_conds.append("entity_people @> %s::jsonb")
            where_params.append(json.dumps([p], ensure_ascii=False))
        for r in (filters.get("entity_regions") or []):
            entity_conds.append("entity_regions @> %s::jsonb")
            where_params.append(json.dumps([r], ensure_ascii=False))
        if entity_conds:
            where_parts.append("(" + " OR ".join(entity_conds) + ")")

    def _fetch(wp, wparams, limit):
        wc = ("WHERE " + " AND ".join(wp)) if wp else ""
        sql = f"""
            SELECT id, record_id, chunk_type, chunk_text, source_filename,
                   1 - (embedding <=> %s::vector) AS score
            FROM podcast_embedded_chunks {wc}
            ORDER BY embedding <=> %s::vector LIMIT %s
        """
        with connections["ai_assistant_db"].cursor() as cur:
            cur.execute(sql, [vec_str] + wparams + [vec_str, limit])
            return cur.fetchall()

    rows = _fetch(where_parts, where_params, n_candidates)

    # fallback
    if where_parts and len(rows) < top_k:
        seen_ids = {r[0] for r in rows}
        for row in _fetch([], [], n_candidates * 2):
            if row[0] not in seen_ids:
                rows = list(rows) + [row]
                seen_ids.add(row[0])
            if len(rows) >= n_candidates:
                break

    # Convert to candidate dicts
    candidates = [
        {
            "id": r[0], "record_id": r[1], "chunk_type": r[2],
            "chunk_text": r[3], "source_filename": r[4], "score": float(r[5]),
        }
        for r in rows
    ]

    # Hybrid rerank → diversity → slice
    candidates = _hybrid_rerank(candidates, question)
    candidates = _apply_diversity(candidates, top_k, max_per_episode)

    return {"retrieved_chunks": candidates, "filters_used": filters}


# ===========================================================================
# Step 3.1：Precision@5
# ===========================================================================

PRECISION_K = 5  # Precision 固定評估前 5 個 chunk

def step3_1_precision(question: str, retrieved_chunks: list[dict], client, model_name: str) -> dict:
    padded = (retrieved_chunks + [{"chunk_text": "（無內容）"}] * PRECISION_K)[:PRECISION_K]
    prompt = _fill(
        STEP3_1_PROMPT,
        question=question,
        **{f"chunk_{i+1}": padded[i]["chunk_text"][:600] for i in range(PRECISION_K)},
    )
    return _call_json(client, model_name, prompt)


# ===========================================================================
# Step 3.2：Coverage
# ===========================================================================

def step3_2_coverage(
    question: str,
    question_type: str,
    must_cover_points: list[str],
    retrieved_chunks: list[dict],
    client,
    model_name: str,
) -> dict:
    points_text = "\n".join(f"- {p}" for p in must_cover_points)
    chunks_text = _format_chunks_for_prompt(retrieved_chunks)
    prompt = _fill(
        STEP3_2_PROMPT,
        question=question,
        question_type=question_type,
        must_cover_points=points_text,
        top_5_chunks=chunks_text,
    )
    return _call_json(client, model_name, prompt)


# ===========================================================================
# Step 4：生成 RAG 答案
# ===========================================================================

def step4_generate_answer(question: str, retrieved_chunks: list[dict], client, model_name: str) -> str:
    chunks_text = _format_chunks_for_prompt(retrieved_chunks)
    prompt = (
        "你是一位專業財經助理，請根據以下 podcast 內容，用繁體中文回答使用者問題。\n"
        "只使用提供的內容作答，不引用外部知識。\n\n"
        "內容不足時的透明化規則：\n"
        "- 若內容與問題明顯不相關，說明「Podcast 中目前找到的內容與這個問題的關聯性較低」。\n"
        "- 若問題問的主題完全沒有在內容中出現，說明「這個主題在目前收錄的 Podcast 中沒有找到直接討論」。\n"
        "- 若問題過於寬泛，回答後建議「可嘗試在問題中加入具體的公司名稱、人名或時間範圍」。\n\n"
        f"問題：{question}\n\n"
        f"參考內容：\n{chunks_text}"
    )
    return _call_text(client, model_name, prompt)


# ===========================================================================
# Step 5：評估答案
# ===========================================================================

def step5_evaluate_answer(
    question: str,
    question_type: str,
    retrieved_chunks: list[dict],
    must_cover_points: list[str],
    answer: str,
    client,
    model_name: str,
) -> dict:
    points_text = "\n".join(f"- {p}" for p in must_cover_points)
    chunks_text = _format_chunks_for_prompt(retrieved_chunks)
    prompt = _fill(
        STEP5_PROMPT,
        question=question,
        question_type=question_type,
        retrieved_chunks=chunks_text,
        must_cover_points=points_text,
        answer=answer,
    )
    return _call_json(client, model_name, prompt)


# ===========================================================================
# Management Command
# ===========================================================================

class Command(BaseCommand):
    help = "RAG 完整評估 Pipeline（Step 1–5，依題型 A/B/C 分析）"

    def add_arguments(self, parser):
        parser.add_argument("--per-type", type=int, default=5, help="每種題型評估幾題（預設 5）")
        parser.add_argument("--seed", type=int, default=42)

    def handle(self, *args, **options):
        per_type = options["per_type"]
        seed = options["seed"]
        random.seed(seed)

        # 載入模型
        self.stdout.write("載入 bge-m3 模型...")
        from sentence_transformers import SentenceTransformer
        embed_model = SentenceTransformer("BAAI/bge-m3")
        self.stdout.write("bge-m3 載入完成")

        import os
        from django.conf import settings
        from apps.ai_assistant.services.ai_assistant import get_gemini_client
        client = get_gemini_client()
        model_name = (
            getattr(settings, "GEMINI_MODEL", None)
            or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        )
        self.stdout.write(f"Gemini 模型：{model_name}\n")

        all_results = []

        for qtype in ["A", "B", "C"]:
            self.stdout.write(f"\n{'='*50}")
            self.stdout.write(f"題型 {qtype}（共 {per_type} 題）")
            self.stdout.write(f"{'='*50}")

            for i in range(per_type):
                self.stdout.write(f"\n  [{i+1}/{per_type}] 題型 {qtype}...")

                record = {"question_type": qtype, "error": None}

                try:
                    # Step 1：生成問題 + validation（最多重試 3 次）
                    self.stdout.write("    Step 1: 生成問題...", ending="\r")
                    step1, source_chunks, actual_record_ids = generate_valid_question(
                        qtype, client, model_name, max_retries=3
                    )
                    if step1 is None:
                        raise RuntimeError("無法生成有效的財經評估問題（重試 3 次均失敗）")

                    question = step1.get("question", "")
                    must_cover_points = step1.get("must_cover_points", [])
                    record.update({
                        "question": question,
                        "must_cover_points": must_cover_points,
                        "actual_source_record_ids": actual_record_ids,
                        "source_chunk_ids": step1.get("source_chunk_ids", []),
                        "source_episode_ids": step1.get("source_episode_ids", []),
                    })
                    self.stdout.write(f"    Step 1 完成：{question[:60]}... (records={actual_record_ids})")
                    time.sleep(0.5)

                    # Step 2
                    self.stdout.write("    Step 2: Retrieval...", ending="\r")
                    cfg = RETRIEVAL_CFG_BY_TYPE[qtype]
                    step2 = step2_retrieve(
                        question, embed_model, client, model_name,
                        top_k=cfg["top_k"],
                        n_candidates=cfg["n_candidates"],
                        max_per_episode=cfg["max_per_episode"],
                    )
                    retrieved_chunks = step2["retrieved_chunks"]
                    filters_used = step2.get("filters_used")
                    record["retrieved_chunks"] = retrieved_chunks
                    record["filters_used"] = filters_used
                    filter_summary = str(filters_used)[:60] if filters_used else "（無 filter）"
                    self.stdout.write(
                        f"    Step 2 完成：取得 {len(retrieved_chunks)} 個 chunk"
                        f"（top_k={cfg['top_k']}, max_per_ep={cfg['max_per_episode']}），"
                        f"filter={filter_summary}"
                    )
                    time.sleep(0.3)

                    # Step 3.1
                    self.stdout.write("    Step 3.1: Precision@5...", ending="\r")
                    step3_1 = step3_1_precision(question, retrieved_chunks, client, model_name)
                    record["precision"] = step3_1
                    p5 = step3_1.get("precision_at_5", 0.0)
                    self.stdout.write(f"    Step 3.1 完成：precision@5 = {p5:.2f}")
                    time.sleep(0.5)

                    # Step 3.2
                    self.stdout.write("    Step 3.2: Coverage...", ending="\r")
                    step3_2 = step3_2_coverage(
                        question, qtype, must_cover_points, retrieved_chunks, client, model_name
                    )
                    record["retrieval_coverage"] = step3_2
                    cov = step3_2.get("coverage_score", 0.0)
                    div = step3_2.get("source_diversity", "-")
                    self.stdout.write(f"    Step 3.2 完成：coverage = {cov:.2f}, diversity = {div}")
                    time.sleep(0.5)

                    # Step 4
                    self.stdout.write("    Step 4: 生成答案...", ending="\r")
                    answer = step4_generate_answer(question, retrieved_chunks, client, model_name)
                    record["answer"] = answer
                    self.stdout.write(f"    Step 4 完成：{answer[:60]}...")
                    time.sleep(0.5)

                    # Step 5
                    self.stdout.write("    Step 5: 評估答案...", ending="\r")
                    step5 = step5_evaluate_answer(
                        question, qtype, retrieved_chunks, must_cover_points, answer, client, model_name
                    )
                    record["answer_eval"] = step5
                    ans_cov = (step5.get("coverage") or {}).get("coverage_score", 0.0)
                    faith = (step5.get("faithfulness") or {}).get("label", "-")
                    rel = (step5.get("relevance") or {}).get("label", "-")
                    self.stdout.write(
                        f"    Step 5 完成：ans_coverage={ans_cov:.2f} "
                        f"faithfulness={faith} relevance={rel}"
                    )

                except Exception as e:
                    logger.warning(f"題型 {qtype} 第 {i+1} 題失敗：{e}")
                    record["error"] = str(e)

                all_results.append(record)
                time.sleep(0.5)

        # 輸出摘要
        self._print_summary(all_results, per_type)

        # 儲存
        out_path = "rag_eval_results.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {"config": {"per_type": per_type, "seed": seed}, "results": all_results},
                f, ensure_ascii=False, indent=2,
            )
        self.stdout.write(f"\n詳細結果已儲存至 {out_path}")

    def _print_summary(self, results: list[dict], per_type: int):
        self.stdout.write(f"\n\n{'='*55}")
        self.stdout.write("評估結果摘要")
        self.stdout.write(f"{'='*55}")

        label_map = {"A": "single", "B": "multi", "C": "comparison"}

        # 各題型需要顯示的指標定義
        # Answerability = Step 5 relevance（最終答案有沒有回答到問題）
        # Coverage      = Step 3.2 coverage_score（retrieval 覆蓋 must_cover_points）
        # Answer Coverage = Step 5 coverage_score（答案覆蓋 must_cover_points）
        # Faithfulness  = Step 5 faithfulness
        # Comparison Quality = Step 5 comparison_quality（僅 C 題）

        for qtype in ["A", "B", "C"]:
            rows = [r for r in results if r["question_type"] == qtype and not r.get("error")]
            if not rows:
                continue

            n = len(rows)
            p5_vals       = [r.get("precision", {}).get("precision_at_5", 0.0) for r in rows]
            rcov_vals     = [r.get("retrieval_coverage", {}).get("coverage_score", 0.0) for r in rows]
            acov_vals     = [(r.get("answer_eval") or {}).get("coverage", {}).get("coverage_score", 0.0) for r in rows]
            faith_labels  = [(r.get("answer_eval") or {}).get("faithfulness", {}).get("label", "") for r in rows]
            answer_labels = [(r.get("answer_eval") or {}).get("relevance", {}).get("label", "") for r in rows]

            self.stdout.write(f"\n【題型 {qtype}（{label_map[qtype]}）】  有效 {n}/{per_type} 題")
            self.stdout.write(f"  Precision@5         {sum(p5_vals)/n:.2f}")

            if qtype in ("B", "C"):
                self.stdout.write(f"  Coverage（檢索）    {sum(rcov_vals)/n:.2f}")

            # Answerability（= Step 5 relevance）
            self.stdout.write(
                f"  Answerability       "
                f"yes={answer_labels.count('yes')}  "
                f"partial={answer_labels.count('partial')}  "
                f"no={answer_labels.count('no')}"
            )

            if qtype == "B":
                self.stdout.write(f"  Answer Coverage     {sum(acov_vals)/n:.2f}")

            if qtype in ("A", "C"):
                self.stdout.write(
                    f"  Faithfulness        "
                    f"faithful={faith_labels.count('faithful')}  "
                    f"partially={faith_labels.count('partially_faithful')}  "
                    f"not={faith_labels.count('not_faithful')}"
                )

            if qtype == "C":
                cq_labels = [
                    ((r.get("answer_eval") or {}).get("comparison_quality") or {}).get("label", "")
                    for r in rows
                ]
                self.stdout.write(
                    f"  Comparison Quality  "
                    f"good={cq_labels.count('good')}  "
                    f"weak={cq_labels.count('weak')}  "
                    f"none={cq_labels.count('none')}"
                )
