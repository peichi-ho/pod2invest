import json
import re
from pathlib import Path
from typing import Optional, Any

from google import genai

from .gemini import gemini_generate_with_retry, sanitize_json_text, repair_to_valid_json


def _append_raw(raw_save_path: Optional[Path], title: str, content: str) -> None:
    if not raw_save_path:
        return
    with open(raw_save_path, "a", encoding="utf-8") as f:
        f.write(f"{title}\n{content}\n\n")


def _normalize_text(s: Any) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    s = s.replace("　", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def _normalize_key_text(s: Any) -> str:
    return _normalize_text(s).lower().replace(" ", "")


def _clean_quote(s: Any) -> str:
    import re as _re
    s = _normalize_text(s)
    s = _re.sub(r"（\d+:\d{2}）", "", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s


def _safe_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _normalize_timeframe(tf: Any) -> Optional[str]:
    tf = _normalize_text(tf)
    if not tf:
        return None

    tf_key = _normalize_key_text(tf)

    if "未來兩年" in tf:
        return "未來兩年"
    if "2026" in tf_key:
        return "2026"
    if "2025" in tf_key:
        return "2025"
    if "短中期" in tf:
        return "短中期"
    if "未來幾年" in tf:
        return "未來幾年"
    if "長期" in tf:
        return "長期"
    if "十年" in tf or "10年" in tf:
        return "長期"

    return tf


def _clean_candidate_call(call: dict) -> Optional[dict]:
    if not isinstance(call, dict):
        return None

    asset = _normalize_text(call.get("asset"))
    direction = _normalize_text(call.get("direction")).lower()
    thesis = _normalize_text(call.get("thesis"))
    timeframe = _normalize_timeframe(call.get("timeframe"))
    evidence_quote = _clean_quote(call.get("evidence_quote"))
    summary_label = _normalize_text(call.get("summary_label"))

    evidence_timestamps = _safe_list(call.get("evidence_timestamps"))
    evidence_timestamps = [_normalize_text(x) for x in evidence_timestamps if _normalize_text(x)]

    if not asset:
        return None
    if direction not in {"bullish", "bearish"}:
        return None

    return {
        "asset": asset,
        "direction": direction,
        "thesis": thesis,
        "summary_label": summary_label,
        "timeframe": timeframe,
        "evidence_timestamps": evidence_timestamps,
        "evidence_quote": evidence_quote,
    }


def _dedupe_strings(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for x in items or []:
        xx = _normalize_text(x)
        if not xx:
            continue
        key = _normalize_key_text(xx)
        if key in seen:
            continue
        seen.add(key)
        out.append(xx)
    return out


def _clamp_timestamps(calls: list[dict], max_per_call: int = 4) -> list[dict]:
    out = []
    for c in calls:
        cc = dict(c)
        ts = _dedupe_strings(_safe_list(cc.get("evidence_timestamps")))
        cc["evidence_timestamps"] = ts[:max_per_call]
        out.append(cc)
    return out


def _quote_fails_minimum_judgment(q: str) -> bool:
    q = _normalize_text(q)
    if not q:
        return True

    if len(q) < 16:
        return True

    if "？" in q or "?" in q:
        return True

    digit_count = sum(ch.isdigit() for ch in q)
    if digit_count >= 3 and len(q) < 28:
        return True

    return False


def _asset_name_looks_too_fragmentary(asset: str) -> bool:
    asset = _normalize_text(asset)
    if not asset:
        return True
    if len(asset) <= 1:
        return True
    return False


def review_outlook_payload(
    client: genai.Client,
    model: str,
    inline_text: str,
    candidate_calls: list[dict],
    raw_save_path: Optional[Path] = None,
    published_at=None,
) -> dict:
    cleaned_candidates = []
    for c in candidate_calls or []:
        cc = _clean_candidate_call(c)
        if cc is not None:
            cleaned_candidates.append(cc)

    # 組成發布日說明
    if published_at is not None:
        try:
            pub_str = published_at.strftime("%Y-%m-%d")
        except Exception:
            pub_str = str(published_at)[:10]
        published_at_block = (
            "【本集發布日期】\n"
            f"本集 Podcast 發布於 {pub_str}。\n"
            "請以此日期為基準判斷「未來」：\n"
            "- 說話者提及的時間點必須在此日期之後，才算未來方向性預測\n"
            "- 例如發布於 2024-09-01：「2025年看好台積電」→ 未來，valid\n"
            "- 例如發布於 2024-09-01：「今年已漲了30%」→ 過去事實，reject\n"
            "- 若逐字稿中出現「今年」「明年」「下半年」等相對時間，請依發布日換算成絕對年份後再判斷\n\n"
        )
    else:
        published_at_block = ""

    prompt = (
        "你是一位投資逐字稿審核員。\n"
        "你的任務不是做最終整合，而是逐筆審核候選 outlook_calls 是否成立。\n\n"
        + published_at_block
        + "【你的任務】\n"
        "對每一筆候選項目，根據完整逐字稿做審核：\n"
        "1. 判斷是否為 valid\n"
        "2. 若 valid，從全文挑出『最能單獨成立』的 evidence_quote\n"
        "3. 若 invalid，說明 reject reason\n"
        "4. 不要做最終壓縮成 1–3 筆，不要做大規模合併\n\n"

        "【句子角色判斷】\n"
        "對每一筆 candidate，你必須先判斷它在全文中的角色，只能是以下之一：\n"
        "1. conclusion：直接表達投資立場或最終判斷\n"
        "2. prediction：直接表達未來預測\n"
        "3. conditional：條件式未來判斷\n"
        "4. supporting_detail_only：只是支撐材料、數字、事件、背景\n"
        "5. example_or_fragment：只是舉例句、片段句、半句、問句\n\n"

        "只有 conclusion / prediction / conditional 才能成為 valid。\n"
        "若是 supporting_detail_only 或 example_or_fragment，必須 reject，"
        "除非你能從全文另找一個更完整的 conclusion / prediction / conditional 句子來替換。\n\n"

        "【valid 的定義】\n"
        "只有當說話者對以下合法標的形成『未來』判斷時，才算 valid：\n"
        "合法標的：上市公司/股票、ETF、主要股市指數（台股大盤/加權指數、S&P500、那斯達克、費半）、大宗商品（黃金、原油、銅）\n"
        "合法類型：\n"
        "1. 對未來表現、未來股價/指數/商品價格方向的明確看法\n"
        "2. 條件式推論：若某條件成立，未來表現可能變好或變差\n"
        "3. 對競爭力、策略、需求、供給、估值風險如何影響未來的結論\n"
        "4. 持有建議：說話者明確表示願意繼續持有、不賣出，隱含對未來成長的信心（例如『現在絕對不是下車的點』『我一張不賣』），且能找到配套的成長 thesis\n"
        "5. 未來獲利/業績預測：說話者明確預期某公司未來 EPS、營收、出貨量將成長（例如『EPS 將呈現好公司狀態』『獲利有機會走升』）\n\n"

        "【必須 reject 的情況】\n"
        "1. 描述過去已發生的事實，且無任何未來方向推論（已買、已賣、已翻倍、已達到目標價）\n"
        "   特別注意：「已翻倍」「已達260」「已賣出」這類過去完成式必須 reject\n"
        "2. 描述當下現狀（現在估值高、目前股價低）—— 除非有明確的未來方向推論\n"
        "3. 單純事件描述（公司發布財報、獲利創新高）\n"
        "4. 單純數據變化或 KPI 描述（EPS 成長X%、本益比X倍）\n"
        "5. 單純產品擴張、合作、AI 布局等事實性敘事，未明說對未來股價/指數的影響\n"
        "6. 問句、拋題、半句、殘句、舉例句\n"
        "7. 產業/族群/概念（半導體業、記憶體、AI概念、無人機、軍工、紡織類股）—— 無法用單一數字回測\n"
        "8. evidence_quote 無法單獨成立\n"
        "9. evidence_quote 整句都在描述已發生的事：出現「當年」「去年」「已漲」「已跌」「漲了X%」「現在漲回來」等詞即為過去事實\n\n"

        "【evidence_quote 硬性要求】\n"
        "1. 必須是完整句意\n"
        "2. 單獨看就知道在講哪家公司，且知道未來會變好、變差，或存在明確條件式風險/機會\n"
        "3. 不可只是數字、事件、片段、例子、問句\n"
        "4. 若候選成立但原 quote 不夠完整，必須回全文換一個更好的原句\n"
        "5. 若全文找不到完整句，就 reject\n\n"

        "【條件句完整性要求】\n"
        "若 evidence_quote 來自條件句、假設句、轉折句，必須保留足以成立判斷的完整語意。\n"
        "不可只截出後半句，例如只留下『才會有比較好的支撐』『股價可能上升』，卻刪掉前面的條件。\n"
        "若前提被刪掉後會改變方向或造成誤解，則該 quote 不可作為 valid evidence_quote。\n\n"
       
        "【反事實與未達成條件判斷】\n"
        "若原文是在說『如果未來達成某條件，才可能改善』，但當下語境是在描述目前仍有疑慮、尚未達成、或市場正在擔心，\n"
        "則不能直接把這種句子判為 bullish。\n"
        "此時應優先判斷整段的主導語氣是 bearish 還是僅為條件式觀望；若無法形成完整 bullish thesis，則 reject。\n\n"

        "【主導方向優先原則】\n"
        "若同一段落中同時出現正面與負面資訊，請以說話者最終主導的投資判斷為準。\n"
        "不可因為段落中出現『未來若改善會更好』這種附帶條件句，就忽略整段其實是在表達保守、疑慮、估值風險或需求壓力。\n\n"

        "【多空平衡原則】\n"
        "若同一股票在全文中同時存在明確的正面與負面 thesis，請不要只因為最後出現一個條件式正面句，就刪掉前面已明確成立的負面 thesis。\n"
        "只要負面 thesis 本身是完整投資判斷，就應保留。\n"
        "同理，只要正面 thesis 本身是完整投資判斷，也可保留。\n\n"

        "【條件式正面句處理】\n"
        "若某段正面 outlook 依賴明確前提，例如『如果獲利穩住』『如果達標』『如果需求回升』，請將其視為 conditional thesis。\n"
        "若同段或前文同時存在保守、疑慮、估值風險、競爭風險、護城河不足等主導語氣，則不能讓這個 conditional bullish 自動取代 bearish thesis。\n\n"

        "【supporting detail 吸收原則】\n"
        "若某候選主要只是 supporting detail，例如財務指標、成長率、Rule of 40、營收創高、估值倍數、股價反應，"
        "而同一股票同方向已有更高層、更完整的 thesis，則該候選應被視為 supporting detail，"
        "在 review 階段可標記為 valid only if it supports a broader thesis，但不應獨立成為最終主要 outlook。\n\n"

        "【thesis 欄位】\n"
        "若 valid，請用一句中文概括核心投資判斷，不要只是重複 quote。\n\n"

        "【rejected_calls 的 reason 只能用以下值】\n"
        "- fact_not_outlook\n"
        "- background_not_outlook\n"
        "- non_single_stock\n"
        "- implied_not_explicit\n"
        "- insufficient_quote\n"
        "- wrong_direction\n\n"

        "【輸出格式】\n"
        "只能輸出合法 JSON。\n"
        "{\n"
        '  "reviewed_calls": [\n'
        "    {\n"
        '      "asset": "標的名稱（公司/指數/商品）",\n'
        '      "direction": "bullish 或 bearish",\n'
        '      "timeframe": "null / 2025 / 2026 / 短中期 / 未來幾年 / 未來兩年 / 長期",\n'
        '      "thesis": "一句完整投資判斷",\n'
        '      "summary_label": "一句讓使用者第一眼看懂的預測摘要，含標的+方向+理由/時間，例如：台積電2026年前看漲，受惠AI伺服器需求",\n'
        '      "evidence_timestamps": ["m:ss"],\n'
        '      "evidence_quote": "說話者原話，可單獨成立的完整句意，最多 60 字",\n'
        '      "sentence_role": "conclusion / prediction / conditional",\n'
        '      "verdict": "valid"\n'
        "    }\n"
        "  ],\n"
        '  "rejected_calls": [\n'
        "    {\n"
        '      "asset": "公司名",\n'
        '      "direction": "bullish 或 bearish",\n'
        '      "evidence_quote": "原候選中的 quote",\n'
        '      "reason": "fact_not_outlook / background_not_outlook / non_single_stock / implied_not_explicit / insufficient_quote / wrong_direction"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"

        "======== 候選 outlook_calls ========\n"
        f"{json.dumps(cleaned_candidates, ensure_ascii=False, indent=2)}\n\n"

        "======== 逐字稿全文 ========\n"
        f"{inline_text}\n"
    )

    resp = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=prompt,
        temperature=0.0,
        max_output_tokens=6000,
        max_tries=6,
    )

    text = (getattr(resp, "text", "") or "")
    _append_raw(raw_save_path, "===OUTLOOK REVIEW RAW===", text)

    clean = sanitize_json_text(text)
    if not clean.strip():
        return {"reviewed_calls": [], "rejected_calls": []}

    try:
        obj = json.loads(clean)
    except json.JSONDecodeError:
        try:
            obj = repair_to_valid_json(client, model, clean)
        except Exception:
            return {"reviewed_calls": [], "rejected_calls": []}

    if not isinstance(obj, dict):
        return {"reviewed_calls": [], "rejected_calls": []}

    reviewed_calls = obj.get("reviewed_calls", []) or []
    rejected_calls = obj.get("rejected_calls", []) or []

    if not isinstance(reviewed_calls, list):
        reviewed_calls = []
    if not isinstance(rejected_calls, list):
        rejected_calls = []

    cleaned_reviewed = []
    for c in reviewed_calls:
        cc = _clean_candidate_call(c)
        if cc is None:
            continue
        if _asset_name_looks_too_fragmentary(cc.get("asset", "")):
            continue
        if _quote_fails_minimum_judgment(cc.get("evidence_quote", "")):
            continue

        sentence_role = _normalize_text(c.get("sentence_role"))
        if sentence_role not in {"conclusion", "prediction", "conditional"}:
            continue

        cc["sentence_role"] = sentence_role
        cc["verdict"] = "valid"
        if not cc.get("summary_label"):
            cc["summary_label"] = _normalize_text(c.get("summary_label"))
        cleaned_reviewed.append(cc)

    cleaned_reviewed = _clamp_timestamps(cleaned_reviewed, max_per_call=4)

    cleaned_rejected = []
    allowed_reasons = {
        "fact_not_outlook",
        "background_not_outlook",
        "non_single_stock",
        "implied_not_explicit",
        "insufficient_quote",
        "wrong_direction",
    }

    for r in rejected_calls:
        if not isinstance(r, dict):
            continue

        reason = _normalize_text(r.get("reason"))
        if reason not in allowed_reasons:
            reason = "insufficient_quote"

        cleaned_rejected.append({
            "asset": _normalize_text(r.get("asset")),
            "direction": _normalize_text(r.get("direction")).lower(),
            "evidence_quote": _normalize_text(r.get("evidence_quote")),
            "reason": reason,
        })

    payload = {
        "reviewed_calls": cleaned_reviewed,
        "rejected_calls": cleaned_rejected,
    }

    _append_raw(
        raw_save_path,
        "===OUTLOOK REVIEW CLEANED===",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )

    return payload


def review_outlook_calls(
    client: genai.Client,
    model: str,
    inline_text: str,
    candidate_calls: list[dict],
    raw_save_path: Optional[Path] = None,
    published_at=None,
) -> list[dict]:
    payload = review_outlook_payload(
        client=client,
        model=model,
        inline_text=inline_text,
        candidate_calls=candidate_calls,
        raw_save_path=raw_save_path,
        published_at=published_at,
    )
    return payload.get("reviewed_calls", [])
