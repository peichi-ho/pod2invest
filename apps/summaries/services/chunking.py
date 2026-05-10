# apps/summaries/services/chunking.py
import json
from pathlib import Path
from typing import List, Optional

from google import genai

from .prompts import build_system_instruction, json_schema_description, build_user_prompt, build_novice_content_schema
from .gemini import gemini_generate_with_retry, sanitize_json_text, repair_to_valid_json
from .postprocess import normalize_schema, postprocess_evidence_ranges
from .enrich import enrich_arguments_if_empty, ensure_min_arguments
from .outlook import extract_outlook_calls
from .outlook_review import review_outlook_calls
from .outlook_consolidate import consolidate_outlook_calls
from .outlook_filter import hard_filter_outlook_calls
from .tag_taxonomy import empty_classification, normalize_classification_dict, dedupe_str_list


def chunk_text_by_chars(text: str, chunk_size: int, overlap: int) -> List[str]:
    if chunk_size <= 0:
        return [text]
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_size)
        chunks.append(text[start:end])
        if end == n:
            break
        start = max(0, end - overlap)
    return chunks


def _dedupe_list(items: List[str]) -> List[str]:
    return dedupe_str_list(items)


def _dedupe_key_data(items: List[dict]) -> List[dict]:
    seen = set()
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        label = (it.get("label") or "").strip()
        value = (it.get("value") or "").strip()
        context = (it.get("context") or "").strip()
        key = (label, value, context)
        if not label and not value and not context:
            continue
        if key not in seen:
            seen.add(key)
            out.append({"label": label, "value": value, "context": context})
    return out


def _merge_topic_notes(all_notes: List[dict]) -> List[dict]:
    bucket: dict[str, dict] = {}
    for n in all_notes or []:
        if not isinstance(n, dict):
            continue

        k = (n.get("topic_key") or "").strip()
        if not k:
            k = (n.get("topic") or "").strip() or "UNKNOWN"

        if k not in bucket:
            bucket[k] = {
                "topic_key": k,
                "topic": (n.get("topic") or "").strip(),
                "position": (n.get("position") or "").strip(),
                "bullets": list(n.get("bullets") or []),
                "key_data": list(n.get("key_data") or []),
                "related_concepts": list(n.get("related_concepts") or []),
                "evidence_timestamps": list(n.get("evidence_timestamps") or []),
            }
        else:
            b = bucket[k]
            t_new = (n.get("topic") or "").strip()
            if len(t_new) > len(b["topic"]):
                b["topic"] = t_new

            p_new = (n.get("position") or "").strip()
            if p_new and (not b["position"] or len(p_new) > len(b["position"])):
                b["position"] = p_new

            b["bullets"].extend(n.get("bullets") or [])
            b["key_data"].extend(n.get("key_data") or [])
            b["related_concepts"].extend(n.get("related_concepts") or [])
            b["evidence_timestamps"].extend(n.get("evidence_timestamps") or [])

    merged = []
    for _, b in bucket.items():
        b["bullets"] = _dedupe_list(b["bullets"])
        b["related_concepts"] = _dedupe_list(b["related_concepts"])
        b["evidence_timestamps"] = _dedupe_list(b["evidence_timestamps"])
        b["key_data"] = _dedupe_key_data(b["key_data"])
        merged.append(b)

    merged.sort(key=lambda x: x.get("topic_key", ""))
    return merged


def summarize_with_optional_chunking(
    client: genai.Client,
    model: str,
    mode: str,
    inline_text: str,
    raw_save_path: Optional[Path] = None,
    chunk_threshold_chars: int = 20000,
    outlook_model: Optional[str] = None,
    published_at=None,
    forced_topics: list = None,
) -> dict:
    _outlook_model = outlook_model or model
    if len(inline_text) <= chunk_threshold_chars:
        return generate_json_summary(
            client=client,
            model=model,
            mode=mode,
            inline_text=inline_text,
            raw_save_path=raw_save_path,
            outlook_model=_outlook_model,
            published_at=published_at,
            forced_topics=forced_topics,
        )

    return map_reduce_summarize(
        client=client,
        model=model,
        mode=mode,
        inline_text=inline_text,
        raw_save_path=raw_save_path,
        chunk_size_chars=22000,
        chunk_overlap_chars=800,
        outlook_model=_outlook_model,
        published_at=published_at,
        forced_topics=forced_topics,
    )


def map_reduce_summarize(
    client: genai.Client,
    model: str,
    mode: str,
    inline_text: str,
    raw_save_path: Optional[Path] = None,
    chunk_size_chars: int = 22000,
    chunk_overlap_chars: int = 800,
    outlook_model: Optional[str] = None,
    published_at=None,
    forced_topics: list = None,
) -> dict:
    system_prompt = build_system_instruction(mode)
    schema_prompt = json_schema_description(mode, forced_topics=forced_topics)

    def append_raw(title: str, content: str):
        if not raw_save_path:
            return
        with open(raw_save_path, "a", encoding="utf-8") as f:
            f.write(f"{title}\n{content}\n\n")

    chunks = chunk_text_by_chars(inline_text, chunk_size_chars, chunk_overlap_chars)
    all_notes: List[dict] = []
    entities_acc = {"companies_or_stocks": [], "countries_or_regions": [], "people": []}
    classification_acc = empty_classification()

    for idx, ch in enumerate(chunks, start=1):
        map_prompt = (
            "===SYSTEM INSTRUCTIONS===\n"
            f"{system_prompt}\n"
            "===END SYSTEM INSTRUCTIONS===\n\n"
            "你現在要做的是『主題筆記蒐集（MAP）』：\n"
            "- 只輸出 JSON，不要 ```json，不要解釋\n"
            "- 主題切法：\n"
            "  【固定結構型】有內容才出現，topic_key 固定用以下名稱：\n"
            "    「總體經濟環境」「操作策略與建議」「風險提示」\n"
            "  【個股型】本段被認真討論（3句以上）的每支股票／ETF／指數各一條，\n"
            "    topic_key 統一命名為「個股：[名稱]」，例如「個股：台積電」\n"
            "  【產業型】本段被認真討論（3句以上）的每個產業主題各一條，\n"
            "    topic_key 統一命名為「產業：[主題名稱]」，例如「產業：記憶體漲價」「產業：AI伺服器需求」\n"
            "  【完整性要求】動筆前先列出本段所有被認真討論（3句以上）的主題，再逐一確認每個都有對應 topic_note，不可遺漏\n"
            "- topic_key 必須穩定：相同主題跨段必須用相同 topic_key，才能正確合併\n"
            "- 同一主題在本段出現多次要合併成一條\n"
            "- topic 是給人看的標題（可與 topic_key 相同）\n"
            "- position 用一句話寫 podcaster 對該主題的明確立場，不可只描述內容\n"
            "- bullets 是重點條列（可 3–8 條）\n"
            "- key_data 只收『明確數字/百分比/日期/指數』，每筆獨立一個物件 {label,value,context}，不可合併成一個字串\n"
            "- evidence_timestamps 從逐字稿中的（m:ss）挑 1–3 個最相關的\n"
            "- 同時抽取 entities 與 classification\n"
            "- classification 各欄位只能從白名單中選，不得自創\n"
            "- tags 欄位固定輸出 []\n\n"
            "===輸出 JSON 格式===\n"
            "{\n"
            '  "chunk": <int>,\n'
            '  "classification": {\n'
            '    "financial_topics": [],\n'
            '    "industries": [],\n'
            '    "content_types": [],\n'
            '    "markets": [],\n'
            '    "asset_types": []\n'
            '  },\n'
            '  "tags": [],\n'
            '  "entities": {"companies_or_stocks":[], "countries_or_regions":[], "people":[]},\n'
            '  "topic_notes": [\n'
            '     {"topic_key":"...", "topic":"...", "position":"...", "bullets":[...], '
            '"key_data":[{"label":"...","value":"...","context":"..."}], '
            '"related_concepts":[...], "evidence_timestamps":[...]}\n'
            "  ]\n"
            "}\n\n"
            "===逐字稿（本段）===\n"
            f"{ch}\n"
        )

        resp = gemini_generate_with_retry(
            client=client,
            model=model,
            prompt_text=map_prompt,
            temperature=0.1,
            max_output_tokens=6000,
            max_tries=6,
        )
        t = (getattr(resp, "text", "") or "")
        append_raw(f"===MAP CHUNK {idx}/{len(chunks)} RAW===", t)

        clean = sanitize_json_text(t)
        if not clean.strip():
            continue

        try:
            obj = json.loads(clean)
        except json.JSONDecodeError:
            try:
                obj = repair_to_valid_json(client, model, clean)
            except Exception:
                # 單一 chunk 修復失敗就跳過，不讓整個流程 crash
                continue

        cls = normalize_classification_dict(obj.get("classification") or {})
        for k in classification_acc.keys():
            classification_acc[k].extend(cls.get(k) or [])

        ent = obj.get("entities") or {}
        for k in ["companies_or_stocks", "countries_or_regions", "people"]:
            entities_acc[k].extend(ent.get(k) or [])
        all_notes.extend(obj.get("topic_notes") or [])

    merged_notes = _merge_topic_notes(all_notes)

    classification_acc = normalize_classification_dict(classification_acc)
    for k in ["companies_or_stocks", "countries_or_regions", "people"]:
        entities_acc[k] = _dedupe_list(entities_acc[k])

    final_prompt = (
        "===SYSTEM INSTRUCTIONS===\n"
        f"{system_prompt}\n"
        "===END SYSTEM INSTRUCTIONS===\n\n"
        "你將收到『合併後的主題筆記』。請輸出最終摘要 JSON（只輸出 JSON，不要 ```json）。\n"
        "硬性要求：\n"
        "- 必須符合下列 schema\n"
        "- 以主題式彙整\n"
        "- entities/classification 必須明確且完整\n"
        "- classification 只能從白名單中選，不得自創\n"
        "- tags 固定輸出 []\n"
        "===SCHEMA===\n"
        f"{schema_prompt}\n"
        "===END SCHEMA===\n\n"
        "===MERGED NOTES===\n"
        f"{json.dumps({'classification': classification_acc, 'tags': [], 'entities': entities_acc, 'topic_notes': merged_notes}, ensure_ascii=False)}\n"
    )

    resp2 = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=final_prompt,
        temperature=0.1,
        max_output_tokens=8000,
        max_tries=6,
    )
    t2 = (getattr(resp2, "text", "") or "")
    append_raw("===FINAL RAW===", t2)

    clean2 = sanitize_json_text(t2)
    if not clean2.strip():
        fallback = {
            "one_sentence_summary": "",
            "investment_takeaways": {
                "bullish": [],
                "bearish": [],
                "watchlist": [],
                "podcaster_stance": "混合/視情況",
            },
            "classification": classification_acc,
            "tags": [],
            "entities": entities_acc,
            "arguments": [
                {
                    "topic": n.get("topic", ""),
                    "position": n.get("position", ""),
                    "summary": "；".join(n.get("bullets", [])[:6]),
                    "key_data": n.get("key_data", []),
                    "related_concepts": n.get("related_concepts", []),
                    "evidence_timestamps": n.get("evidence_timestamps", []),
                }
                for n in merged_notes[:10]
            ],
            "outlook_calls": [],
        }
        fallback = normalize_schema(fallback)

        _om = outlook_model or model
        candidate_outlook_calls = extract_outlook_calls(
            client=client,
            model=_om,
            inline_text=inline_text,
            raw_save_path=raw_save_path,
        )

        reviewed_outlook_calls = review_outlook_calls(
            client=client,
            model=_om,
            inline_text=inline_text,
            candidate_calls=candidate_outlook_calls,
            raw_save_path=raw_save_path,
            published_at=published_at,
        )

        fallback["outlook_calls"] = hard_filter_outlook_calls(consolidate_outlook_calls(
            client=client,
            model=_om,
            reviewed_calls=reviewed_outlook_calls,
            raw_save_path=raw_save_path,
        ))

        fallback = postprocess_evidence_ranges(fallback)
        return fallback

    try:
        obj = json.loads(clean2)
    except json.JSONDecodeError:
        try:
            obj = repair_to_valid_json(client, model, clean2)
        except Exception:
            # FINAL reduce 修復失敗，用 merged notes 組出 fallback
            obj = {
                "one_sentence_summary": "",
                "investment_takeaways": {
                    "bullish": [],
                    "bearish": [],
                    "watchlist": [],
                    "podcaster_stance": "混合/視情況",
                },
                "classification": classification_acc,
                "tags": [],
                "entities": entities_acc,
                "arguments": [
                    {
                        "topic": n.get("topic", ""),
                        "position": n.get("position", ""),
                        "summary": "；".join(n.get("bullets", [])[:6]),
                        "key_data": n.get("key_data", []),
                        "related_concepts": n.get("related_concepts", []),
                        "evidence_timestamps": n.get("evidence_timestamps", []),
                    }
                    for n in merged_notes[:10]
                ],
                "outlook_calls": [],
            }

    obj = normalize_schema(obj)

    _om = outlook_model or model
    candidate_outlook_calls = extract_outlook_calls(
        client=client,
        model=_om,
        inline_text=inline_text,
        raw_save_path=raw_save_path,
    )

    reviewed_outlook_calls = review_outlook_calls(
        client=client,
        model=_om,
        inline_text=inline_text,
        candidate_calls=candidate_outlook_calls,
        raw_save_path=raw_save_path,
        published_at=published_at,
    )

    obj["outlook_calls"] = hard_filter_outlook_calls(consolidate_outlook_calls(
        client=client,
        model=_om,
        reviewed_calls=reviewed_outlook_calls,
        raw_save_path=raw_save_path,
    ))

    obj = postprocess_evidence_ranges(obj)
    return obj


def generate_json_summary(
    client: genai.Client,
    model: str,
    mode: str,
    inline_text: str,
    raw_save_path: Optional[Path] = None,
    temperature: float = 0.2,
    max_output_tokens: int = 6000,
    outlook_model: Optional[str] = None,
    published_at=None,
    forced_topics: list = None,
) -> dict:
    system_prompt = build_system_instruction(mode)
    schema_prompt = json_schema_description(mode, forced_topics=forced_topics)

    def append_raw(title: str, content: str):
        if not raw_save_path:
            return
        with open(raw_save_path, "a", encoding="utf-8") as f:
            f.write(f"{title}\n{content}\n\n")

    head = inline_text[:20000]
    tail = inline_text[-8000:] if len(inline_text) > 28000 else ""
    excerpt = head + ("\n\n---\n（中略）\n---\n\n" + tail if tail else "")

    prompt1 = (
        "===SYSTEM INSTRUCTIONS===\n"
        f"{system_prompt}\n"
        "===END SYSTEM INSTRUCTIONS===\n\n"
        "===JSON FORMAT===\n"
        f"{schema_prompt}\n"
        "===END JSON FORMAT===\n\n"
        "===USER DATA（截斷版，避免過長）===\n"
        f"{build_user_prompt(excerpt)}\n"
        "===END USER DATA===\n\n"
        "請先輸出『最短可用版本』，且必須是『合法 JSON』：\n"
        "- 只能輸出 JSON，不要輸出 ```json 或 ``` 或任何說明\n"
        "- classification 必須使用固定欄位輸出\n"
        "- classification 每個欄位的值只能從白名單中選，不得自創\n"
        "- tags 固定輸出 []\n"
        "- arguments 切法：\n"
        "  固定結構型（有內容才出現）：「總體經濟環境」「操作策略與建議」「風險提示」\n"
        "  個股型：每支被認真討論（3句以上）的股票各一條，topic 命名為「個股：XXX」\n"
        "  產業型：每個被認真討論的產業主題各一條，topic 命名為「產業：XXX」，例如「產業：記憶體漲價」「產業：PCB族群」\n"
        "  完整性：動筆前先列出本段所有被認真討論（3句以上）的主題，再逐一確認每個都有對應 argument，不可遺漏\n"
        "- 每條 position 必須有明確方向，不可只是描述主題內容\n"
        "- outlook_calls 欄位先固定輸出 []\n"
    )

    resp1 = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=prompt1,
        temperature=0.05,
        max_output_tokens=6000,
        max_tries=6,
    )
    text1 = (getattr(resp1, "text", "") or "")
    append_raw("===STEP1 RAW===", text1)

    clean1 = sanitize_json_text(text1)

    try:
        base_obj = json.loads(clean1)
    except json.JSONDecodeError:
        try:
            base_obj = repair_to_valid_json(client, model, clean1)
        except Exception as e:
            append_raw("===STEP1 REPAIR FAILED (fallback to skeleton)===", str(e))
            base_obj = {
                "one_sentence_summary": "",
                "investment_takeaways": {
                    "bullish": [],
                    "bearish": [],
                    "watchlist": [],
                    "podcaster_stance": "混合/視情況",
                },
                "classification": empty_classification(),
                "tags": [],
                "entities": {
                    "companies_or_stocks": [],
                    "countries_or_regions": [],
                    "people": [],
                },
                "arguments": [],
                "outlook_calls": [],
            }

    full_head = inline_text[:12000]
    full_tail = inline_text[-3000:] if len(inline_text) > 15000 else ""
    full_excerpt = full_head + ("\n\n---\n（中略）\n---\n\n" + full_tail if full_tail else "")

    prompt2 = (
        "你將收到一個 JSON（已符合格式）。\n"
        "請在『不改動欄位結構』的前提下，根據逐字稿補充內容，使摘要更完整：\n"
        "- 每一個 arguments.summary 都必須是『詳細段落型摘要』，至少 120字以上，220字以下\n"
        "- arguments 依主題式合併、補充 key_data、related_concepts、evidence_timestamps\n"
        "- entities/classification 補齊\n"
        "- classification 各欄位只能從白名單中選，不得自創\n"
        "- tags 欄位請維持為 []，系統後處理會自動根據 classification 生成\n"
        "- outlook_calls 欄位請維持為 []，後續會由另一個步驟單獨補上\n"
        "- 只能輸出 JSON，不要輸出 ```json 或 ``` 或任何說明\n\n"
        "===現有 JSON===\n"
        f"{json.dumps(base_obj, ensure_ascii=False)}\n\n"
        "===逐字稿（截斷版）===\n"
        f"{full_excerpt}\n"
    )

    try:
        resp2 = gemini_generate_with_retry(
            client=client,
            model=model,
            prompt_text=prompt2,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            max_tries=6,
        )
        text2 = (getattr(resp2, "text", "") or "")
        append_raw("===STEP2 RAW===", text2)

        clean2 = sanitize_json_text(text2)
        if not clean2.strip():
            raise RuntimeError("STEP2 returned empty/invalid JSON text.")

        try:
            final_obj = json.loads(clean2)
        except json.JSONDecodeError:
            final_obj = repair_to_valid_json(client, model, clean2)

        final_obj = enrich_arguments_if_empty(client, model, mode, final_obj, inline_text, raw_save_path)
        final_obj = ensure_min_arguments(client, model, mode, final_obj, inline_text, raw_save_path, min_topics=5)

        final_obj = normalize_schema(final_obj)

        _om = outlook_model or model
        candidate_outlook_calls = extract_outlook_calls(
            client=client,
            model=_om,
            inline_text=inline_text,
            raw_save_path=raw_save_path,
        )

        reviewed_outlook_calls = review_outlook_calls(
            client=client,
            model=_om,
            inline_text=inline_text,
            candidate_calls=candidate_outlook_calls,
            raw_save_path=raw_save_path,
            published_at=published_at,
        )

        final_obj["outlook_calls"] = hard_filter_outlook_calls(consolidate_outlook_calls(
            client=client,
            model=_om,
            reviewed_calls=reviewed_outlook_calls,
            raw_save_path=raw_save_path,
        ))

        final_obj = postprocess_evidence_ranges(final_obj)
        return final_obj

    except Exception as e:
        append_raw("===STEP2 FAILED (fallback to STEP1)===", str(e))

        base_obj = enrich_arguments_if_empty(client, model, mode, base_obj, inline_text, raw_save_path)
        base_obj = ensure_min_arguments(client, model, mode, base_obj, inline_text, raw_save_path, min_topics=5)

        base_obj = normalize_schema(base_obj)

        _om = outlook_model or model
        candidate_outlook_calls = extract_outlook_calls(
            client=client,
            model=_om,
            inline_text=inline_text,
            raw_save_path=raw_save_path,
        )

        reviewed_outlook_calls = review_outlook_calls(
            client=client,
            model=_om,
            inline_text=inline_text,
            candidate_calls=candidate_outlook_calls,
            raw_save_path=raw_save_path,
            published_at=published_at,
        )

        base_obj["outlook_calls"] = hard_filter_outlook_calls(consolidate_outlook_calls(
            client=client,
            model=_om,
            reviewed_calls=reviewed_outlook_calls,
            raw_save_path=raw_save_path,
        ))

        base_obj = postprocess_evidence_ranges(base_obj)
        return base_obj


def generate_novice_content(
    client: genai.Client,
    model: str,
    inline_text: str,
    forced_topics: list,
    pro_takeaways: dict = None,
    raw_save_path: Optional[Path] = None,
    chunk_threshold_chars: int = 20000,
) -> dict:
    """
    從完整逐字稿為小白模式重新生成 investment_takeaways + arguments。
    只輸出這兩個欄位，其餘從 pro 複製（由 _merge_novice_into_pro 處理）。
    pro_takeaways：若提供，要求 LLM 保持相同標的與分類，只改寫說明文字。
    """
    schema_prompt = build_novice_content_schema(forced_topics, pro_takeaways=pro_takeaways)

    def append_raw(title: str, content: str):
        if not raw_save_path:
            return
        with open(raw_save_path, "a", encoding="utf-8") as f:
            f.write(f"{title}\n{content}\n\n")

    # 依長度決定送入的逐字稿長度（頭重尾輕，確保開場/結論都涵蓋）
    if len(inline_text) <= chunk_threshold_chars:
        excerpt = inline_text
    else:
        head = inline_text[:18000]
        tail = inline_text[-6000:]
        excerpt = head + "\n\n---\n（中略）\n---\n\n" + tail

    system = (
        "你是一個台灣金融投資 Podcast 內容解說助理，專門為沒有投資背景的初學者整理節目重點。\n"
        "你必須嚴格依照使用者的輸出格式，只輸出 JSON（不要加任何多餘文字）。"
    )

    prompt = (
        f"===SYSTEM INSTRUCTIONS===\n{system}\n===END SYSTEM INSTRUCTIONS===\n\n"
        f"===輸出格式===\n{schema_prompt}\n===END 輸出格式===\n\n"
        "===逐字稿===\n"
        f"{excerpt}\n"
    )

    resp = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=prompt,
        temperature=0.2,
        max_output_tokens=6000,
        max_tries=6,
    )
    text = (getattr(resp, "text", "") or "")
    append_raw("===NOVICE CONTENT RAW===", text)

    clean = sanitize_json_text(text)
    if not clean.strip():
        return {}

    try:
        obj = json.loads(clean)
    except json.JSONDecodeError:
        try:
            obj = repair_to_valid_json(client, model, clean)
        except Exception:
            return {}

    return obj if isinstance(obj, dict) else {}


def _merge_novice_into_pro(pro_result: dict, novice_content: dict) -> dict:
    """
    以 pro_result 為底，overlay novice 的 investment_takeaways 和 arguments 內容欄位。
    topic、evidence_timestamps 從 pro 保留以確保一致性。
    """
    import copy
    novice = copy.deepcopy(pro_result)

    if novice_content.get("investment_takeaways"):
        novice["investment_takeaways"] = novice_content["investment_takeaways"]

    novice_args_map = {
        arg["topic"]: arg
        for arg in (novice_content.get("arguments") or [])
        if arg.get("topic")
    }

    merged_args = []
    for pro_arg in (pro_result.get("arguments") or []):
        topic = pro_arg.get("topic", "")
        n = novice_args_map.get(topic, {})
        merged_args.append({
            "topic": topic,
            "position": n.get("position") or pro_arg.get("position", ""),
            "summary": n.get("summary") or pro_arg.get("summary", ""),
            "key_data": n.get("key_data") or pro_arg.get("key_data", []),
            "related_concepts": n.get("related_concepts") or pro_arg.get("related_concepts", []),
            "evidence_timestamps": pro_arg.get("evidence_timestamps", []),
        })

    novice["arguments"] = merged_args
    return novice
