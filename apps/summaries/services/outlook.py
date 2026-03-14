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
    return tf


def _clean_one_call_light(call: dict) -> Optional[dict]:
    """
    第一階段只做格式整理，不做 precision filter。
    """
    if not isinstance(call, dict):
        return None

    asset = _normalize_text(call.get("asset"))
    direction = _normalize_text(call.get("direction")).lower()
    thesis = _normalize_text(call.get("thesis"))
    timeframe = _normalize_timeframe(call.get("timeframe"))
    evidence_quote = _normalize_text(call.get("evidence_quote"))

    evidence_timestamps = _safe_list(call.get("evidence_timestamps"))
    evidence_timestamps = [_normalize_text(x) for x in evidence_timestamps if _normalize_text(x)]

    if not asset:
        return None
    if not direction:
        return None

    return {
        "asset": asset,
        "direction": direction,
        "thesis": thesis,
        "timeframe": timeframe,
        "evidence_timestamps": evidence_timestamps,
        "evidence_quote": evidence_quote,
    }


def _score_call(call: dict) -> int:
    """
    只用來決定 merge 時保留哪筆，不代表真實性。
    """
    score = 0

    if _normalize_text(call.get("asset")):
        score += 5
    if _normalize_text(call.get("direction")):
        score += 5
    if _normalize_text(call.get("thesis")):
        score += min(len(_normalize_text(call.get("thesis"))), 80)
    if _normalize_text(call.get("evidence_quote")):
        score += min(len(_normalize_text(call.get("evidence_quote"))), 80)
    if _normalize_text(call.get("timeframe")):
        score += 8
    score += min(len(_safe_list(call.get("evidence_timestamps"))) * 4, 40)

    return score


def _core_text_for_merge(call: dict) -> str:
    thesis = _normalize_text(call.get("thesis"))
    if thesis:
        return thesis
    return _normalize_text(call.get("evidence_quote"))


def _merge_timestamps(ts1: list[str], ts2: list[str]) -> list[str]:
    seen = set()
    out = []
    for x in (ts1 or []) + (ts2 or []):
        xx = _normalize_text(x)
        if not xx:
            continue
        if xx in seen:
            continue
        seen.add(xx)
        out.append(xx)
    return out


def _ts_to_seconds(ts: str) -> Optional[int]:
    ts = _normalize_text(ts)
    if not ts:
        return None

    parts = ts.split(":")
    try:
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + int(s)
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + int(s)
    except ValueError:
        return None
    return None


def _timestamps_close(ts_list_a: list[str], ts_list_b: list[str], max_gap_sec: int = 90) -> bool:
    a_secs = []
    for x in ts_list_a or []:
        sec = _ts_to_seconds(x)
        if sec is not None:
            a_secs.append(sec)

    b_secs = []
    for x in ts_list_b or []:
        sec = _ts_to_seconds(x)
        if sec is not None:
            b_secs.append(sec)

    if not a_secs or not b_secs:
        return False

    for a in a_secs:
        for b in b_secs:
            if abs(a - b) <= max_gap_sec:
                return True
    return False


def _is_same_or_similar_concept(a: dict, b: dict) -> bool:
    """
    第一階段只負責 merge，不做真假判斷。
    merge 條件：
    - 同 asset
    - 同 direction
    - thesis / quote 有明顯重疊
    - 或同 timeframe 且 timestamp 接近
    - 或核心句很短但時間接近，視為同段落碎片
    """
    a_asset = _normalize_key_text(a.get("asset"))
    b_asset = _normalize_key_text(b.get("asset"))
    if not a_asset or not b_asset or a_asset != b_asset:
        return False

    a_dir = _normalize_key_text(a.get("direction"))
    b_dir = _normalize_key_text(b.get("direction"))
    if not a_dir or not b_dir or a_dir != b_dir:
        return False

    a_core = _normalize_key_text(_core_text_for_merge(a))
    b_core = _normalize_key_text(_core_text_for_merge(b))

    if a_core and b_core:
        if a_core == b_core:
            return True
        if a_core in b_core or b_core in a_core:
            return True

    a_tf = _normalize_key_text(a.get("timeframe"))
    b_tf = _normalize_key_text(b.get("timeframe"))
    same_tf = bool(a_tf and b_tf and a_tf == b_tf)

    ts_close = _timestamps_close(
        _safe_list(a.get("evidence_timestamps")),
        _safe_list(b.get("evidence_timestamps")),
        max_gap_sec=90,
    )

    if same_tf and ts_close:
        return True

    if a_core and b_core:
        short = min(len(a_core), len(b_core))
        if short >= 8:
            overlap_len = min(12, short)
            if a_core[:overlap_len] == b_core[:overlap_len]:
                return True

    if ts_close:
        if (0 < len(a_core) <= 18) or (0 < len(b_core) <= 18):
            return True

    return False


def _merge_two_calls(a: dict, b: dict) -> dict:
    best = a if _score_call(a) >= _score_call(b) else b
    other = b if best is a else a

    merged = dict(best)

    merged["evidence_timestamps"] = _merge_timestamps(
        _safe_list(a.get("evidence_timestamps")),
        _safe_list(b.get("evidence_timestamps")),
    )

    if not _normalize_text(merged.get("thesis")):
        merged["thesis"] = _normalize_text(other.get("thesis"))
    if not _normalize_text(merged.get("timeframe")):
        merged["timeframe"] = _normalize_text(other.get("timeframe"))
    if not _normalize_text(merged.get("evidence_quote")):
        merged["evidence_quote"] = _normalize_text(other.get("evidence_quote"))

    return merged


def _merge_similar_calls(calls: list[dict]) -> list[dict]:
    result: list[dict] = []

    for call in calls:
        merged = False
        for i, prev in enumerate(result):
            if _is_same_or_similar_concept(prev, call):
                result[i] = _merge_two_calls(prev, call)
                merged = True
                break
        if not merged:
            result.append(call)

    return result


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


def _collect_groundable_assets_from_payload(payload: dict) -> list[str]:
    """
    從第一階段模型原始 payload 裡收集『可作為股票/公司 grounding 的名稱』。
    優先來源：
    1. mentioned_assets
    2. entities.companies_or_stocks
    """
    out = []

    mentioned_assets = _safe_list(payload.get("mentioned_assets"))
    out.extend([_normalize_text(x) for x in mentioned_assets if _normalize_text(x)])

    entities = payload.get("entities") or {}
    companies = _safe_list(entities.get("companies_or_stocks"))
    out.extend([_normalize_text(x) for x in companies if _normalize_text(x)])

    return _dedupe_strings(out)


def _asset_is_grounded(asset: str, grounded_assets: list[str]) -> bool:
    """
    不用關鍵字黑名單，而是做 entity grounding：
    candidate asset 必須能對應到已抽出的公司/股票實體。
    採寬鬆比對：
    - 完全相等
    - 包含關係
    """
    asset_key = _normalize_key_text(asset)
    if not asset_key:
        return False

    grounded_keys = [_normalize_key_text(x) for x in grounded_assets if _normalize_key_text(x)]
    if not grounded_keys:
        # 若 grounding source 自己是空的，寧可不刪，避免 recall 掉太多
        return True

    for g in grounded_keys:
        if asset_key == g:
            return True
        if asset_key in g or g in asset_key:
            return True

    return False


def _ground_outlook_calls(calls: list[dict], grounded_assets: list[str]) -> list[dict]:
    """
    只保留 asset 能被 entities / mentioned_assets grounding 的項目。
    這不是 keyword filter，而是結構一致性檢查。
    """
    out = []
    for c in calls:
        asset = _normalize_text(c.get("asset"))
        if _asset_is_grounded(asset, grounded_assets):
            out.append(c)
    return out


def _clamp_timestamps(calls: list[dict], max_per_call: int = 6) -> list[dict]:
    """
    UI / 後處理友善：每筆最多留少量代表性時間點。
    """
    out = []
    for c in calls:
        cc = dict(c)
        ts = _dedupe_strings(_safe_list(cc.get("evidence_timestamps")))
        cc["evidence_timestamps"] = ts[:max_per_call]
        out.append(cc)
    return out


def _sort_calls(calls: list[dict]) -> list[dict]:
    def sort_key(x: dict):
        asset = _normalize_text(x.get("asset"))
        direction = _normalize_text(x.get("direction"))
        timeframe = _normalize_text(x.get("timeframe") or "")
        ts = _safe_list(x.get("evidence_timestamps"))
        first_ts = _normalize_text(ts[0]) if ts else ""
        return (asset, direction, timeframe, first_ts)

    return sorted(calls, key=sort_key)


def _postprocess_outlook_calls_light(calls: list[dict], grounded_assets: list[str]) -> list[dict]:
    """
    第一階段只做：
    1. 格式整理
    2. 同概念 merge
    3. entity grounding
    4. timestamp clamp
    """
    cleaned = []
    for c in calls:
        cc = _clean_one_call_light(c)
        if cc is not None:
            cleaned.append(cc)

    merged = _merge_similar_calls(cleaned)
    merged = _ground_outlook_calls(merged, grounded_assets)
    merged = _clamp_timestamps(merged, max_per_call=6)
    merged = _sort_calls(merged)
    return merged


def extract_outlook_payload(
    client: genai.Client,
    model: str,
    inline_text: str,
    raw_save_path: Optional[Path] = None,
) -> dict:
    prompt = (
        "你是一個專門抽取投資 Podcast 中『單一股票未來方向性看法』的資訊抽取器。\n"
        "你的任務是從逐字稿中找出所有 mentioned_assets、entities，以及 outlook_calls。\n\n"

        "【outlook_call 定義】\n"
        "只有當說話者『明確對某一檔單一股票未來表現或股價方向提出看法』時，才算 outlook_call。\n"
        "例如：看好、看壞、會漲、會跌、有上行空間、有下行風險、長期有成長潛力、可長期佈局、"
        "若基本面持續改善股價可能上升、未來表現會更好、估值會提升、目標價上看/下看。\n\n"

        "【輸出格式】\n"
        "只能輸出合法 JSON，不可包含任何解釋文字或 markdown。\n"
        "格式如下：\n"
        "{\n"
        '  "mentioned_assets": ["安森美", "HIMS"],\n'
        '  "entities": {\n'
        '    "companies_or_stocks": ["安森美", "HIMS"],\n'
        '    "countries_or_regions": [],\n'
        '    "people": []\n'
        '  },\n'
        '  "outlook_calls": [\n'
        "    {\n"
        '      "asset": "公司名稱",\n'
        '      "direction": "bullish 或 bearish",\n'
        '      "thesis": "一句話概括核心投資主張，若無法概括可留空字串",\n'
        '      "timeframe": "例如 2026(短中期)，或 null",\n'
        '      "evidence_timestamps": ["5:53"],\n'
        '      "evidence_quote": "最多 25 字"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"

        "【抽取流程（務必遵守）】\n"
        "Step1：完整掃描全文，列出所有出現的單一股票名稱或股票代號到 mentioned_assets。\n"
        "Step2：同時抽出 entities，其中 companies_or_stocks 只放公司或股票。\n"
        "Step3：逐一檢查每一檔股票附近語句，判斷是否存在未來方向性看法。\n"
        "Step4：只要符合 outlook_call 定義，就全部收錄，不要只選最強訊號的一筆。\n"
        "Step5：如果同一檔股票在同一段落或相鄰語句中，反覆表達同一個核心 outlook 概念，請合併成 1 筆，不要拆成很多筆。\n\n"

        "【合法 asset】\n"
        "只接受：\n"
        "- 單一上市公司名稱（如 台積電、安森美、HIMS、博通）\n"
        "- 股票代號或 ticker（如 2330、NVDA、TSLA）\n\n"

        "【以下全部禁止收錄到 outlook_calls】\n"
        "- 產業或族群（AI、半導體、車用晶片、台股）\n"
        "- 商品（黃金、白銀、原油、銅）\n"
        "- 指數（S&P500、費半、羅素2000）\n"
        "- 國家或總體經濟（美國經濟、降息、通膨）\n"
        "- 模糊對象（企業、供應鏈、廠商）\n\n"

        "【非常重要規則】\n"
        "1）不得從『利多事件』自行推論 bullish。\n"
        "2）evidence_quote 必須盡量貼近原文。\n"
        "3）長期成長潛力、可長期佈局、投資價值存在，屬於合法 bullish outlook，不可忽略。\n"
        "4）條件式看法也可收錄，例如：若公司達標股價可能上升。\n"
        "5）若逐字稿沒有任何符合條件，請輸出 {\"mentioned_assets\": [], \"entities\": {\"companies_or_stocks\": [], \"countries_or_regions\": [], \"people\": []}, \"outlook_calls\": []}。\n"
        "6）mentioned_assets 只要是單一股票名稱或 ticker，就應列出；即使沒有 outlook 也要列出。\n\n"

        "【同概念合併規則】\n"
        "同一檔股票如果在同一段落或相鄰語句中，多句話其實在表達同一個核心 outlook thesis，必須合併成 1 筆，不可拆成很多筆。\n"
        "例如：同一檔股票的 EPS 預估、合理價、目標價、長期成長潛力，若都在支持同一個主張，應合併為 1 筆。\n\n"

        "【direction 判斷】\n"
        "- 正面未來表現 → bullish\n"
        "- 負面未來表現 → bearish\n\n"

        "【timeframe】\n"
        "- 明年、2026 → (短中期)\n"
        "- 未來幾年 → (中期)\n"
        "- 長期、十年 → (長期)\n"
        "- 無法判斷 → null\n\n"

        "===逐字稿===\n"
        f"{inline_text}\n"
    )

    resp = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=prompt,
        temperature=0.0,
        max_output_tokens=2500,
        max_tries=6,
    )

    text = (getattr(resp, "text", "") or "")
    _append_raw(raw_save_path, "===OUTLOOK RAW===", text)

    clean = sanitize_json_text(text)
    if not clean.strip():
        return {
            "mentioned_assets": [],
            "entities": {"companies_or_stocks": [], "countries_or_regions": [], "people": []},
            "outlook_calls": [],
        }

    try:
        obj = json.loads(clean)
    except json.JSONDecodeError:
        obj = repair_to_valid_json(client, model, clean)

    if not isinstance(obj, dict):
        return {
            "mentioned_assets": [],
            "entities": {"companies_or_stocks": [], "countries_or_regions": [], "people": []},
            "outlook_calls": [],
        }

    mentioned_assets = _dedupe_strings(_safe_list(obj.get("mentioned_assets")))

    entities = obj.get("entities") or {}
    if not isinstance(entities, dict):
        entities = {}

    normalized_entities = {
        "companies_or_stocks": _dedupe_strings(_safe_list(entities.get("companies_or_stocks"))),
        "countries_or_regions": _dedupe_strings(_safe_list(entities.get("countries_or_regions"))),
        "people": _dedupe_strings(_safe_list(entities.get("people"))),
    }

    grounded_assets = _collect_groundable_assets_from_payload({
        "mentioned_assets": mentioned_assets,
        "entities": normalized_entities,
    })

    outlook_calls = obj.get("outlook_calls", []) or []
    if not isinstance(outlook_calls, list):
        outlook_calls = []

    outlook_calls = _postprocess_outlook_calls_light(outlook_calls, grounded_assets)

    payload = {
        "mentioned_assets": mentioned_assets,
        "entities": normalized_entities,
        "outlook_calls": outlook_calls,
    }

    _append_raw(
        raw_save_path,
        "===OUTLOOK CLEANED===",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )

    return payload


def extract_outlook_calls(
    client: genai.Client,
    model: str,
    inline_text: str,
    raw_save_path: Optional[Path] = None,
) -> list[dict]:
    payload = extract_outlook_payload(
        client=client,
        model=model,
        inline_text=inline_text,
        raw_save_path=raw_save_path,
    )
    return payload.get("outlook_calls", [])
