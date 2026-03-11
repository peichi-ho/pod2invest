# apps/summaries/services/chunking.py
import json
from pathlib import Path
from typing import List, Optional

from google import genai

from .prompts import build_system_instruction, json_schema_description, build_user_prompt
from .gemini import gemini_generate_with_retry, sanitize_json_text, repair_to_valid_json
from .postprocess import normalize_schema, strict_filter_outlook_calls, postprocess_evidence_ranges
from .enrich import enrich_arguments_if_empty, ensure_min_arguments


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
    seen = set()
    out = []
    for x in items or []:
        x2 = (x or "").strip()
        if not x2:
            continue
        if x2 not in seen:
            seen.add(x2)
            out.append(x2)
    return out


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
    chunk_threshold_chars: int = 30000,
) -> dict:
    if len(inline_text) <= chunk_threshold_chars:
        return generate_json_summary(
            client=client,
            model=model,
            mode=mode,
            inline_text=inline_text,
            raw_save_path=raw_save_path,
        )

    return map_reduce_summarize(
        client=client,
        model=model,
        mode=mode,
        inline_text=inline_text,
        raw_save_path=raw_save_path,
        chunk_size_chars=22000,
        chunk_overlap_chars=800,
    )


def map_reduce_summarize(
    client: genai.Client,
    model: str,
    mode: str,
    inline_text: str,
    raw_save_path: Optional[Path] = None,
    chunk_size_chars: int = 22000,
    chunk_overlap_chars: int = 800,
) -> dict:
    system_prompt = build_system_instruction(mode)
    schema_prompt = json_schema_description()

    def append_raw(title: str, content: str):
        if not raw_save_path:
            return
        with open(raw_save_path, "a", encoding="utf-8") as f:
            f.write(f"{title}\n{content}\n\n")

    chunks = chunk_text_by_chars(inline_text, chunk_size_chars, chunk_overlap_chars)
    all_notes: List[dict] = []
    all_outlook_calls: List[dict] = []
    entities_acc = {"companies_or_stocks": [], "countries_or_regions": [], "people": []}
    tags_acc: List[str] = []

    for idx, ch in enumerate(chunks, start=1):
        map_prompt = (
            "===SYSTEM INSTRUCTIONS===\n"
            f"{system_prompt}\n"
            "===END SYSTEM INSTRUCTIONS===\n\n"
            "你現在要做的是『主題筆記蒐集（MAP）』：\n"
            "- 只輸出 JSON，不要 ```json，不要解釋\n"
            "- 用『主題式』整理：同一主題若在本段出現多次要合併\n"
            "- 每個主題必須給一個 topic_key（穩定可合併的鍵）\n"
            "- topic 是給人看的標題\n"
            "- position 用一句話寫 podcaster 立場\n"
            "- bullets 是重點條列（可 3–8 條）\n"
            "- key_data 只收『明確數字/百分比/日期/指數』\n"
            "- evidence_timestamps 從逐字稿中的（m:ss）挑 1–3 個最相關的\n"
            "- 同時抽取 entities 與 tags（tags 必須以 # 開頭）\n"
            "- outlook_calls：只收『未來』對『具體可交易標的』的 bullish/bearish 觀點（asset 必須可交易，泛稱不行；timeframe 抓不到可 null；過去回顧或過去事實都不得放）\n\n"
            "===輸出 JSON 格式===\n"
            "{\n"
            '  "chunk": <int>,\n'
            '  "tags": ["#..."],\n'
            '  "entities": {"companies_or_stocks":[], "countries_or_regions":[], "people":[]},\n'
            '  "topic_notes": [\n'
            '     {"topic_key":"...", "topic":"...", "position":"...", "bullets":[...], "key_data":[{"label":"...","value":"...","context":"..."}], "related_concepts":[...], "evidence_timestamps":[...]}\n'
            "  ],\n"
            '  "outlook_calls": [\n'
            '    {"asset":"...", "direction":"bullish|bearish", "timeframe": null, "evidence_timestamps":[...], "evidence_quote":"(最多25字且避免雙引號)"}\n'
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
            obj = repair_to_valid_json(client, model, clean)

        tags_acc.extend(obj.get("tags") or [])
        ent = obj.get("entities") or {}
        for k in ["companies_or_stocks", "countries_or_regions", "people"]:
            entities_acc[k].extend(ent.get(k) or [])
        all_notes.extend(obj.get("topic_notes") or [])
        all_outlook_calls.extend(obj.get("outlook_calls") or [])

    merged_notes = _merge_topic_notes(all_notes)

    tags_acc = _dedupe_list(tags_acc)
    for k in ["companies_or_stocks", "countries_or_regions", "people"]:
        entities_acc[k] = _dedupe_list(entities_acc[k])

    seen_oc = set()
    ocs = []
    for c in all_outlook_calls:
        if not isinstance(c, dict):
            continue
        asset = (c.get("asset") or "").strip()
        direction = (c.get("direction") or "").strip()
        timeframe = c.get("timeframe", None)
        key = (asset, direction, str(timeframe))
        if not asset or not direction:
            continue
        if key not in seen_oc:
            seen_oc.add(key)
            ocs.append(c)

    final_prompt = (
        "===SYSTEM INSTRUCTIONS===\n"
        f"{system_prompt}\n"
        "===END SYSTEM INSTRUCTIONS===\n\n"
        "你將收到『合併後的主題筆記』。請輸出最終摘要 JSON（只輸出 JSON，不要 ```json）。\n"
        "硬性要求：\n"
        "- 必須符合下列 schema\n"
        "- 以主題式彙整\n"
        "- entities/tags 必須明確且完整\n"
        "- outlook_calls：只收『未來』對『具體可交易標的』的 bullish/bearish 觀點（asset 必須可交易；timeframe 抓不到可 null；過去回顧或過去事實都不得放；若無則 []）\n\n"
        "===SCHEMA===\n"
        f"{schema_prompt}\n"
        "===END SCHEMA===\n\n"
        "===MERGED NOTES===\n"
        f"{json.dumps({'tags': tags_acc, 'entities': entities_acc, 'topic_notes': merged_notes, 'outlook_calls': ocs}, ensure_ascii=False)}\n"
    )

    resp2 = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=final_prompt,
        temperature=0.1,
        max_output_tokens=6000,
        max_tries=6,
    )
    t2 = (getattr(resp2, "text", "") or "")
    append_raw("===FINAL RAW===", t2)

    clean2 = sanitize_json_text(t2)
    if not clean2.strip():
        fallback = {
            "one_sentence_summary": "",
            "investment_takeaways": {"bullish": [], "bearish": [], "watchlist": [], "podcaster_stance": "混合/視情況"},
            "tags": tags_acc,
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
            "outlook_calls": ocs,
        }
        fallback = normalize_schema(fallback)
        fallback = strict_filter_outlook_calls(fallback, inline_text)
        fallback = postprocess_evidence_ranges(fallback)
        return fallback

    try:
        obj = json.loads(clean2)
    except json.JSONDecodeError:
        obj = repair_to_valid_json(client, model, clean2)

    obj = normalize_schema(obj)
    obj = strict_filter_outlook_calls(obj, inline_text)
    obj = postprocess_evidence_ranges(obj)
    return obj


def generate_json_summary(
    client: genai.Client,
    model: str,
    mode: str,
    inline_text: str,
    raw_save_path: Optional[Path] = None,
    temperature: float = 0.2,
    max_output_tokens: int = 4200,
) -> dict:
    system_prompt = build_system_instruction(mode)
    schema_prompt = json_schema_description()

    def append_raw(title: str, content: str):
        if not raw_save_path:
            return
        with open(raw_save_path, "a", encoding="utf-8") as f:
            f.write(f"{title}\n{content}\n\n")

    # STEP 1 用較短節選
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
        "- tags 每個都要以 # 開頭（例如 #美國經濟）\n"
        "- arguments 至少 5 個 topic\n"
        "- 你必須包含 outlook_calls 欄位（就算是空陣列也要輸出 \\\"outlook_calls\\\": []）\n"
        "- outlook_calls 必須符合嚴格條件：只收未來語氣 + 可交易股票(asset) + 明確 bullish/bearish；timeframe 抓不到可 null；若沒有符合就 []\n"
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
                "tags": [],
                "entities": {
                    "companies_or_stocks": [],
                    "countries_or_regions": [],
                    "people": [],
                },
                "arguments": [],
                "outlook_calls": [],
            }

    # STEP 2 再用另一個較保守節選
    full_head = inline_text[:12000]
    full_tail = inline_text[-3000:] if len(inline_text) > 15000 else ""
    full_excerpt = full_head + ("\n\n---\n（中略）\n---\n\n" + full_tail if full_tail else "")

    prompt2 = (
        "你將收到一個 JSON（已符合格式）。\n"
        "請在『不改動欄位結構』的前提下，根據逐字稿補充內容，使摘要更完整：\n"
        "- 每一個 arguments.summary 都必須是『詳細段落型摘要』，至少 120字以上，220字以下\n"
        "- arguments 依主題式合併、補充 key_data、related_concepts、evidence_timestamps\n"
        "- entities/tags 補齊（必須明確名稱）\n"
        "\n"
        "- outlook_calls：抽取「講者對股票的未來方向性判斷」（嚴格篩選）\n"
        "  1) 必須是未來語氣（未來/明年/下一季/接下來/預估/有機會/會來到/將/目標價…），過去發生的事絕對不可放入\n"
        "  2) asset 必須是可交易股票：只接受個股/代號/美股ticker，產業泛稱一律不行\n"
        "  3) direction 必須明確 bullish 或 bearish；只有估值區間但無方向不要放\n"
        "  4) timeframe：有明確時間就填，抓不到可 null\n"
        "  5) 若無符合者：outlook_calls 必須是 []\n"
        "  6) evidence_quote 最多25字且避免雙引號\n"
        "\n"
        "- 你必須保留並輸出 outlook_calls 欄位（就算是空陣列也要輸出）\n"
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
        final_obj = strict_filter_outlook_calls(final_obj, inline_text)
        final_obj = postprocess_evidence_ranges(final_obj)
        return final_obj

    except Exception as e:
        append_raw("===STEP2 FAILED (fallback to STEP1)===", str(e))

        base_obj = enrich_arguments_if_empty(client, model, mode, base_obj, inline_text, raw_save_path)
        base_obj = ensure_min_arguments(client, model, mode, base_obj, inline_text, raw_save_path, min_topics=5)

        base_obj = normalize_schema(base_obj)
        base_obj = strict_filter_outlook_calls(base_obj, inline_text)
        base_obj = postprocess_evidence_ranges(base_obj)
        return base_obj