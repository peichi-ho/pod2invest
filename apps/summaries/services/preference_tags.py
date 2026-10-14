# apps/summaries/services/preference_tags.py
"""
摘要完成後另外產生三個推薦用欄位（對應註冊問卷）：
  - primary_tags：從這集的 tags 裡挑 3～5 個最能代表核心內容的
  - analysis_style：ANALYSIS_STYLES 中的 0～2 個
  - investor_strategy：INVESTOR_STRATEGIES 中的 0～2 個

為什麼獨立一步、不塞進原本的摘要 prompt：tags 是長集數切段後各段分類的聯集，
只代表「提到過」，要判斷「主要在講什麼」得看整集彙整後的結果。輸入只用已完成
的摘要（不用逐字稿），所以舊集數也能用同一個函式補標（見 backfill_preference_tags）。
"""
import json
import logging

from .gemini import gemini_generate_with_retry, sanitize_json_text, extract_json_object
from .tag_taxonomy import ANALYSIS_STYLES, INVESTOR_STRATEGIES, dedupe_str_list

logger = logging.getLogger(__name__)

PREF_TAG_FIELDS = ("primary_tags", "analysis_style", "investor_strategy")
PRIMARY_TAGS_MAX = 5
STYLE_MAX = 2


def _summary_digest(summary: dict, max_arg_chars: int = 300) -> str:
    """把摘要整理成給 LLM 看的精簡文字：一句話摘要、投資重點、各段論點。"""
    lines = [f"一句話摘要：{summary.get('one_sentence_summary') or ''}"]

    takeaways = summary.get("investment_takeaways") or {}
    for key, label in (("bullish", "看多"), ("bearish", "看空"), ("watchlist", "觀察")):
        items = takeaways.get(key) or []
        if isinstance(items, list) and items:
            lines.append(f"{label}：" + "；".join(str(x) for x in items))

    for arg in summary.get("arguments") or []:
        if not isinstance(arg, dict):
            continue
        text = str(arg.get("summary") or "")[:max_arg_chars]
        lines.append(f"【{arg.get('topic') or ''}】{arg.get('position') or ''} {text}")

    return "\n".join(lines)


def _build_prompt(summary: dict, tags: list) -> str:
    return (
        "你是投資 Podcast 的內容分類員。以下是一集節目的摘要，請判斷這集「主要」在講什麼。\n\n"
        "===本集摘要===\n"
        f"{_summary_digest(summary)}\n"
        "===本集標籤（整集有提到過的分類）===\n"
        f"{json.dumps(tags, ensure_ascii=False)}\n\n"
        "請輸出合法 JSON（只輸出 JSON，不要 ```json）：\n"
        '{"primary_tags": [], "analysis_style": [], "investor_strategy": []}\n\n'
        "規則：\n"
        f"1) primary_tags：只能從「本集標籤」中挑 3～{PRIMARY_TAGS_MAX} 個最能代表這集核心內容的。"
        "只是順帶提到的不要選；「投資觀點」「深度分析」這類幾乎每集都適用的內容類型，除非真的是這集最大特色，否則不要選。"
        "如果這集明顯以某個市場為主（例如討論的公司幾乎都是台股，或幾乎都是美股），要把該市場標籤放進來；兩邊份量差不多就都不要放。\n"
        f"2) analysis_style：從 {ANALYSIS_STYLES} 中選這集「最主要」的分析方法，通常只選 1 個；"
        f"只有兩種方法份量真的差不多時才選 2 個（上限 {STYLE_MAX}）。順帶提到總經背景或公司財報不算主要方法：\n"
        "   - 技術分析：K 線、均線、月線、季線、成交量、量能、籌碼、支撐壓力、動能、技術指標\n"
        "   - 基本面分析：營收、EPS、毛利率、本益比、財報、競爭優勢、公司價值\n"
        "   - 總經分析：利率、聯準會、通膨、匯率、景氣循環、政策與地緣政治對市場的影響\n"
        "   - 指數化投資：買大盤或市值型 ETF、被動投資、不選股\n"
        f"3) investor_strategy：從 {INVESTOR_STRATEGIES} 中選 0～{STYLE_MAX} 個，只有這集內容「明顯適合這類投資人」才選：\n"
        "   - 價差交易：波段操作、題材股或成長股進出、進出場時機、停損停利\n"
        "   - 存股領息：高股息、殖利率、配息、填息、存股、金融股\n"
        "   - 長期定期定額：定期定額、長期持有、資產配置、複利\n"
        "   - 小資入門：小額投資、零股、第一桶金、新手理財觀念\n"
        "4) 不確定就輸出 []，寧缺勿濫；不得自創選項。\n"
    )


def _parse_json(text: str) -> dict:
    clean = sanitize_json_text(text or "")
    for candidate in (clean, extract_json_object(clean) or ""):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            continue
    return {}


def _pick(values, allowed: list, limit: int) -> list:
    allowed_set = set(allowed)
    out = [v for v in dedupe_str_list(values if isinstance(values, list) else []) if v in allowed_set]
    return out[:limit]


def generate_preference_tags(*, client, model: str, summary: dict) -> dict:
    """
    回傳 {"primary_tags": [...], "analysis_style": [...], "investor_strategy": [...]}。
    LLM 輸出一律再用白名單過濾一次（primary_tags 必須是這集原本 tags 的子集）。
    """
    tags = list(summary.get("tags") or [])
    resp = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=_build_prompt(summary, tags),
        temperature=0.0,
        max_output_tokens=1000,
        max_tries=6,
    )
    obj = _parse_json(getattr(resp, "text", "") or "")
    if not obj:
        raise RuntimeError("preference tags: LLM 沒有回傳可解析的 JSON")

    return {
        "primary_tags": _pick(obj.get("primary_tags"), tags, PRIMARY_TAGS_MAX),
        "analysis_style": _pick(obj.get("analysis_style"), ANALYSIS_STYLES, STYLE_MAX),
        "investor_strategy": _pick(obj.get("investor_strategy"), INVESTOR_STRATEGIES, STYLE_MAX),
    }


def attach_preference_tags(*, client, model: str, summary: dict) -> dict:
    """
    摘要流程用：把三個欄位寫進 summary dict（已經有就不重跑）。
    失敗不影響摘要本身，只記 log、欄位留空，之後可用 backfill_preference_tags 補。
    """
    if not isinstance(summary, dict) or all(summary.get(f) for f in PREF_TAG_FIELDS):
        return summary
    try:
        summary.update(generate_preference_tags(client=client, model=model, summary=summary))
    except Exception:
        logger.exception("preference tags 產生失敗，欄位先留空")
        for f in PREF_TAG_FIELDS:
            summary.setdefault(f, [])
    return summary
