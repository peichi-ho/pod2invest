import re
from typing import Any

_BAD_ASSETS = {
    "AI", "AI股", "AI鏈", "AI概念", "二奈米鏈", "半導體", "記憶體",
    "科技股", "無人機", "軍工", "紡織類股", "航運", "生技", "供應鏈",
    "族群", "概念股", "類股",
}
_BAD_ASSET_SUBSTRINGS = ["族群", "類股", "概念", "供應鏈", "鏈股"]

_PAST_WORDS = [
    "昨天", "上季", "剛剛",
    "當年", "去年已", "已漲", "已跌", "漲回來", "跌回來",
    "已經漲了", "已經跌了", "漲了%", "跌了%",
]
_PAST_YEAR_RE = re.compile(r"20(1\d|2[0-3])年")


def _is_bad_call(call: Any) -> tuple[bool, str]:
    if not isinstance(call, dict):
        return True, "非 dict"

    asset = (call.get("asset") or "").strip()
    quote = (call.get("evidence_quote") or "").strip()
    tf = (call.get("timeframe") or "").strip()
    ts = call.get("evidence_timestamps") or []

    if asset in _BAD_ASSETS:
        return True, f"不可追蹤標的: {asset}"
    if any(sub in asset for sub in _BAD_ASSET_SUBSTRINGS):
        return True, f"族群/概念標的: {asset}"
    if any(w in quote for w in _PAST_WORDS):
        return True, f"evidence_quote 含過去事實關鍵詞"
    m = _PAST_YEAR_RE.search(tf)
    if m and int(m.group(0)[:4]) < 2024:
        return True, f"timeframe 是過去年份: {tf}"
    if not ts and not quote:
        return True, "timestamps 與 quote 皆為空"

    return False, ""


def hard_filter_outlook_calls(calls: list) -> list:
    """
    Rule-based 確定性過濾，不依賴 LLM。
    放在 consolidate 之後作為第四道防線。
    """
    if not isinstance(calls, list):
        return []
    return [c for c in calls if not _is_bad_call(c)[0]]
