# apps/ai_assistant/management/commands/build_podcast_embeddings.py
"""
Podcast Chunking + Embedding Pipeline
======================================
從 summariesdb 讀取 summaries_summaryrecord，
切成 3 種 chunk，用 bge-m3 本機 embedding，
寫入 ai_assistant_db 的 podcast_embedded_chunks。

使用方式：
    python manage.py build_podcast_embeddings
    python manage.py build_podcast_embeddings --batch-size 8
    python manage.py build_podcast_embeddings --record-id 123   # 只跑特定一筆
"""

import json
import logging
from typing import List, Dict, Any, Optional

from django.core.management.base import BaseCommand
from django.db import connections

logger = logging.getLogger(__name__)


# =============================================================================
# Chunk text 組裝
# =============================================================================

def _key_data_to_text(key_data: List[Dict]) -> str:
    """[{"label":"...", "value":"...", "context":"..."}] → 自然語言句子"""
    parts = []
    for kd in key_data or []:
        if not isinstance(kd, dict):
            continue
        label = (kd.get("label") or "").strip()
        value = (kd.get("value") or "").strip()
        context = (kd.get("context") or "").strip()
        if not label and not value:
            continue
        text = f"{label}{value}"
        if context:
            text += f"（{context}）"
        parts.append(text)
    return "；".join(parts)


def build_argument_chunk_text(arg: Dict) -> str:
    """
    主題：{topic}
    {summary}
    關鍵數據：{key_data 轉自然語言}   ← 有才加
    相關概念：{related_concepts 用頓號串接}
    """
    topic = (arg.get("topic") or "").strip()
    summary = (arg.get("summary") or "").strip()
    key_data = arg.get("key_data") or []
    related_concepts = arg.get("related_concepts") or []

    lines = []
    if topic:
        lines.append(f"主題：{topic}")
    if summary:
        lines.append(summary)
    if key_data:
        kd_text = _key_data_to_text(key_data)
        if kd_text:
            lines.append(f"關鍵數據：{kd_text}")
    if related_concepts:
        lines.append(f"相關概念：{'、'.join(related_concepts)}")

    return "\n".join(lines)


def build_takeaway_chunk_text(investment_takeaways: Dict) -> str:
    """
    投資觀點
    多頭：{bullish 用句號串接}      ← 空陣列就跳過
    空頭：{bearish 用句號串接}
    觀察清單：{watchlist 用句號串接}
    播客立場：{podcaster_stance}
    """
    bullish = investment_takeaways.get("bullish") or []
    bearish = investment_takeaways.get("bearish") or []
    watchlist = investment_takeaways.get("watchlist") or []
    stance = (investment_takeaways.get("podcaster_stance") or "").strip()

    lines = ["投資觀點"]
    if bullish:
        lines.append(f"多頭：{'。'.join(bullish)}")
    if bearish:
        lines.append(f"空頭：{'。'.join(bearish)}")
    if watchlist:
        lines.append(f"觀察清單：{'。'.join(watchlist)}")
    if stance:
        lines.append(f"播客立場：{stance}")

    return "\n".join(lines)


def build_summary_chunk_text(one_sentence_summary: str) -> str:
    return (one_sentence_summary or "").strip()


# =============================================================================
# 從一筆 SummaryRecord 產生所有 chunk
# =============================================================================

def build_chunks_from_record(record: Dict) -> List[Dict]:
    """
    回傳 list of chunk dict，每個 dict 包含要寫入 DB 的欄位。
    """
    chunks = []

    record_id = record["id"]
    entity_people = json.dumps((record.get("entities") or {}).get("people") or [], ensure_ascii=False)
    entity_companies = json.dumps((record.get("entities") or {}).get("companies_or_stocks") or [], ensure_ascii=False)
    entity_regions = json.dumps((record.get("entities") or {}).get("countries_or_regions") or [], ensure_ascii=False)
    tags = json.dumps(record.get("tags") or [], ensure_ascii=False)
    source_filename = record.get("source_filename") or None
    podcaster = json.dumps(record.get("podcaster"), ensure_ascii=False) if record.get("podcaster") is not None else None
    published_at = record.get("published_at") or None

    base = dict(
        record_id=record_id,
        entity_people=entity_people,
        entity_companies=entity_companies,
        entity_regions=entity_regions,
        tags=tags,
        source_filename=source_filename,
        podcaster=podcaster,
        published_at=published_at,
    )

    # --- Chunk Type 1: argument ---
    arguments = record.get("arguments") or []
    for arg in arguments:
        if not isinstance(arg, dict):
            continue
        chunk_text = build_argument_chunk_text(arg)
        if not chunk_text.strip():
            continue
        chunks.append({
            **base,
            "chunk_type": "argument",
            "topic": (arg.get("topic") or "").strip() or None,
            "chunk_text": chunk_text,
        })

    # --- Chunk Type 2: takeaway ---
    investment_takeaways = record.get("investment_takeaways") or {}
    if investment_takeaways:
        chunk_text = build_takeaway_chunk_text(investment_takeaways)
        if chunk_text.strip() and chunk_text != "投資觀點":
            chunks.append({
                **base,
                "chunk_type": "takeaway",
                "topic": None,
                "chunk_text": chunk_text,
            })

    # --- Chunk Type 3: summary ---
    one_sentence_summary = record.get("one_sentence_summary") or ""
    chunk_text = build_summary_chunk_text(one_sentence_summary)
    if chunk_text:
        chunks.append({
            **base,
            "chunk_type": "summary",
            "topic": None,
            "chunk_text": chunk_text,
        })

    return chunks


# =============================================================================
# bge-m3 embedding（lazy init）
# =============================================================================

_embed_model = None


def get_embed_model():
    global _embed_model
    if _embed_model is None:
        from FlagEmbedding import BGEM3FlagModel
        print("載入 bge-m3 模型中（首次需要下載，約 2~3 GB）...")
        _embed_model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
        print("bge-m3 載入完成")
    return _embed_model


def embed_texts(texts: List[str], batch_size: int = 8) -> List[List[float]]:
    """
    輸入加上 'passage: ' 前綴後送入 bge-m3，回傳 dense vectors (1024 維)。
    """
    model = get_embed_model()
    prefixed = [f"passage: {t}" for t in texts]
    output = model.encode(
        prefixed,
        batch_size=batch_size,
        max_length=8192,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    dense_vecs = output["dense_vecs"]  # numpy array, shape (n, 1024)
    return [vec.tolist() for vec in dense_vecs]


# =============================================================================
# 寫入 ai_assistant_db
# =============================================================================

def _vec_to_pg(vec: List[float]) -> str:
    """將 Python list 轉為 pgvector 字串格式 '[v1,v2,...]'"""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


def insert_chunks(chunks_with_embeddings: List[Dict], cursor):
    sql = """
        INSERT INTO podcast_embedded_chunks
            (record_id, chunk_type, topic, chunk_text, embedding,
             entity_people, entity_companies, entity_regions, tags,
             source_filename, podcaster, published_at, embedded_at)
        VALUES
            (%s, %s, %s, %s, %s::vector,
             %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
             %s, %s::jsonb, %s, now())
    """
    for c in chunks_with_embeddings:
        cursor.execute(sql, [
            c["record_id"],
            c["chunk_type"],
            c["topic"],
            c["chunk_text"],
            _vec_to_pg(c["embedding"]),
            c["entity_people"],
            c["entity_companies"],
            c["entity_regions"],
            c["tags"],
            c["source_filename"],
            c["podcaster"],
            c["published_at"],
        ])


# =============================================================================
# Django Management Command
# =============================================================================

class Command(BaseCommand):
    help = "從 summariesdb 讀取資料，切 chunk、embedding，寫入 ai_assistant_db"

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=8,
            help="embedding batch size（預設 8）",
        )
        parser.add_argument(
            "--record-id",
            type=int,
            default=None,
            help="只處理指定 record id（測試用）",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        only_record_id = options["record_id"]

        # --- Step 1: 讀取 summariesdb ---
        self.stdout.write("Step 1: 讀取 summaries_summaryrecord ...")

        with connections["summariesdb"].cursor() as src_cursor:
            if only_record_id:
                src_cursor.execute("""
                    SELECT id, one_sentence_summary, investment_takeaways,
                           tags, entities, arguments, source_filename,
                           podcaster, published_at
                    FROM summaries_summaryrecord
                    WHERE id = %s
                """, [only_record_id])
            else:
                # 跳過已經 embedded 的 record
                src_cursor.execute("""
                    SELECT id, one_sentence_summary, investment_takeaways,
                           tags, entities, arguments, source_filename,
                           podcaster, published_at
                    FROM summaries_summaryrecord
                    WHERE id NOT IN (
                        SELECT DISTINCT record_id
                        FROM podcast_embedded_chunks
                        WHERE embedded_at IS NOT NULL
                    )
                    ORDER BY id
                """)

            columns = [col[0] for col in src_cursor.description]
            rows = src_cursor.fetchall()

        records = [dict(zip(columns, row)) for row in rows]
        self.stdout.write(f"  → 找到 {len(records)} 筆待處理 record")

        if not records:
            self.stdout.write(self.style.SUCCESS("沒有需要處理的 record，結束。"))
            return

        # --- Step 2: 切 chunk ---
        self.stdout.write("Step 2: 切 chunk ...")
        all_chunks = []
        for rec in records:
            chunks = build_chunks_from_record(rec)
            all_chunks.extend(chunks)
        self.stdout.write(f"  → 共產生 {len(all_chunks)} 個 chunk")

        # --- Step 3: Embedding ---
        self.stdout.write(f"Step 3: bge-m3 embedding（batch_size={batch_size}）...")
        texts = [c["chunk_text"] for c in all_chunks]
        embeddings = embed_texts(texts, batch_size=batch_size)

        for chunk, emb in zip(all_chunks, embeddings):
            chunk["embedding"] = emb

        self.stdout.write(f"  → embedding 完成")

        # --- Step 4: 寫入 ai_assistant_db ---
        self.stdout.write("Step 4: 寫入 podcast_embedded_chunks ...")
        with connections["ai_assistant_db"].cursor() as dst_cursor:
            insert_chunks(all_chunks, dst_cursor)

        self.stdout.write(self.style.SUCCESS(
            f"完成！共寫入 {len(all_chunks)} 筆 chunk。"
        ))
