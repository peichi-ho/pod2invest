# apps/summaries/services/postprocess.py
import re
from typing import List, Optional

from .srt import mss_to_seconds, seconds_to_mss


def normalize_schema(summary: dict) -> dict:
    if not isinstance(summary, dict):
        return {}

    summary.setdefault("one_sentence_summary", "")
    summary.setdefault("investment_takeaways", {})
    summary["investment_takeaways"].setdefault("bullish", [])
    summary["investment_takeaways"].setdefault("bearish", [])
    summary["investment_takeaways"].setdefault("watchlist", [])
    summary["investment_takeaways"].setdefault("podcaster_stance", "混合/視情況")

    summary.setdefault("tags", [])
    summary.setdefault("entities", {})
    summary["entities"].setdefault("companies_or_stocks", [])
    summary["entities"].setdefault("countries_or_regions", [])
    summary["entities"].setdefault("people", [])

    summary.setdefault("arguments", [])
    summary.setdefault("outlook_calls", [])

    args = summary.get("arguments") or []
    if isinstance(args, list):
        for a in args:
            if not isinstance(a, dict):
                continue
            kd = a.get("key_data", [])
            if isinstance(kd, dict):
                new_list = []
                for k, v in kd.items():
                    if v is None:
                        continue
                    new_list.append({"label": str(k), "value": str(v), "context": ""})
                a["key_data"] = new_list
            elif isinstance(kd, str):
                a["key_data"] = [{"label": "", "value": kd.strip(), "context": ""}]
            elif not isinstance(kd, list):
                a["key_data"] = []

    oc = summary.get("outlook_calls") or []
    if isinstance(oc, dict):
        summary["outlook_calls"] = [oc]
    elif isinstance(oc, str):
        summary["outlook_calls"] = []
    elif not isinstance(oc, list):
        summary["outlook_calls"] = []

    return summary


def strict_filter_outlook_calls(summary: dict, inline_text: str) -> dict:
    """
    嚴格過濾 outlook_calls（只留逐字稿能證明的「未來 + 明確方向 + 可交易股票」）
    """
    if not isinstance(summary, dict):
        return summary

    calls = summary.get("outlook_calls") or []
    if not isinstance(calls, list):
        summary["outlook_calls"] = []
        return summary

    FUTURE_CUES = [
        "未來", "明年", "下一季", "下季", "接下來", "預估", "估計", "有機會", "會來到", "將", "目標價", "上看", "下看",
        "有望", "可能", "接下來幾個月", "接下來三個月", "今年底", "明年初"
    ]
    BULLISH_CUES = ["看多", "偏多", "看好", "上看", "走高", "上漲", "利多", "正面", "突破", "挑戰", "創新高", "轉強", "有望"]
    BEARISH_CUES = ["看空", "偏空", "下看", "走低", "下跌", "利空", "負面", "回檔", "修正", "壓力", "下探", "轉弱", "風險"]

    GENERIC_ASSET_BAD = [
        "台股", "大盤", "加權", "指數", "市場", "美股", "港股", "A股",
        "AI", "人工智慧", "半導體", "科技股", "金融股", "族群", "板塊", "概念股", "供應鏈", "美股七雄"
    ]

    ticker_re = re.compile(r"^\d{3,6}$")       # 2330, 0050...
    us_ticker_re = re.compile(r"^[A-Z]{1,5}$") # AAPL, NVDA...
    cn_name_re = re.compile(r"^[\u4e00-\u9fff]{2,8}$")

    ALIAS = {
        "台積電": ["台積電", "TSMC", "2330"],
        "鴻海": ["鴻海", "富士康", "Foxconn", "2317"],
        "聯發科": ["聯發科", "MediaTek", "2454"],
        "0050": ["0050", "元大台灣50", "元大台灣50ETF"],
        "AAPL": ["AAPL", "蘋果", "苹果", "Apple"],
        "NVDA": ["NVDA", "輝達", "辉达", "NVIDIA"],
    }

    TS_INLINE_RE = re.compile(r"（\d+:\d{2}）")

    def has_any(txt: str, kws: List[str]) -> bool:
        return any(k in txt for k in kws)

    def normalize_direction_from_text(txt: str) -> Optional[str]:
        bull = has_any(txt, BULLISH_CUES)
        bear = has_any(txt, BEARISH_CUES)
        if bull and not bear:
            return "bullish"
        if bear and not bull:
            return "bearish"
        return None

    def is_tradeable_asset(asset: str) -> bool:
        if any(bad in asset for bad in GENERIC_ASSET_BAD):
            return False
        if ticker_re.match(asset) or us_ticker_re.match(asset) or cn_name_re.match(asset):
            return True
        m = re.match(r"^([\u4e00-\u9fff]{2,8})\s*\(\s*\d{3,6}\s*\)$", asset)
        return bool(m)

    def canonical_asset(asset: str) -> str:
        a = (asset or "").strip()
        m = re.match(r"^([\u4e00-\u9fff]{2,8})\s*\(\s*\d{3,6}\s*\)$", a)
        if m:
            a = m.group(1)
        for cano, aliases in ALIAS.items():
            if a in aliases:
                return cano
        return a

    def all_aliases(asset: str) -> List[str]:
        cano = canonical_asset(asset)
        if cano in ALIAS:
            return list(dict.fromkeys(ALIAS[cano]))
        return [cano]

    def extract_timeframe_from_text(txt: str) -> Optional[str]:
        m = re.search(r"(20\d{2})\s*年?", txt or "")
        if m:
            return m.group(1)
        for k in ["明年", "下一季", "下季", "下半年", "上半年", "三個月", "半年", "一年", "兩年", "Q1", "Q2", "Q3", "Q4", "今年底", "明年初"]:
            if k in (txt or ""):
                return k
        return None

    def window_after_timestamp(ts: str, win_chars: int = 360) -> str:
        ts = (ts or "").strip()
        if not ts:
            return ""
        needle = f"（{ts}）"
        idx = inline_text.find(needle)
        if idx == -1:
            return ""
        start = idx + len(needle)
        end = min(len(inline_text), start + win_chars)
        return inline_text[start:end]

    def clean_text(txt: str) -> str:
        txt = (txt or "").replace("\n", " ").replace("\r", " ")
        txt = TS_INLINE_RE.sub("", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    def make_quote(txt: str, aliases: List[str]) -> str:
        txt = clean_text(txt)
        if not txt:
            return ""
        pos = -1
        for a in aliases:
            p = txt.find(a)
            if p != -1:
                pos = p
                break
        if pos == -1:
            for k in FUTURE_CUES:
                p = txt.find(k)
                if p != -1:
                    pos = p
                    break
        if pos == -1:
            pos = 0
        s = max(0, pos - 8)
        out = txt[s:s + 40]
        out = out.replace('"', "").replace("“", "").replace("”", "").strip()
        return out[:25]

    def quote_score(q: str) -> int:
        score = 0
        if has_any(q, BULLISH_CUES) or has_any(q, BEARISH_CUES):
            score += 2
        if has_any(q, FUTURE_CUES):
            score += 1
        if "目標價" in q:
            score += 2
        return score

    kept = []
    for c in calls:
        if not isinstance(c, dict):
            continue

        asset_raw = (c.get("asset") or "").strip()
        if not asset_raw or not is_tradeable_asset(asset_raw):
            continue

        ev = c.get("evidence_timestamps") or []
        if isinstance(ev, str):
            ev = [ev]
        if not isinstance(ev, list) or not ev:
            continue

        used_ts = None
        win = ""
        for ts in ev:
            ts = str(ts).strip()
            w = window_after_timestamp(ts)
            if w:
                used_ts = ts
                win = w
                break
        if not win:
            continue

        aliases = all_aliases(asset_raw)
        if not any(a in win for a in aliases):
            continue

        if not has_any(win, FUTURE_CUES):
            continue

        direction = normalize_direction_from_text(win)
        if not direction:
            continue

        timeframe = extract_timeframe_from_text(win)
        quote = make_quote(win, aliases)
        cano = canonical_asset(asset_raw)

        kept.append({
            "asset": cano,
            "direction": direction,
            "timeframe": timeframe,
            "evidence_timestamps": [used_ts] if used_ts else [],
            "evidence_quote": quote,
        })

    best = {}
    for item in kept:
        key = (item["asset"], item["direction"], str(item.get("timeframe")))
        cur = best.get(key)
        if not cur or quote_score(item.get("evidence_quote", "")) > quote_score(cur.get("evidence_quote", "")):
            best[key] = item

    summary["outlook_calls"] = list(best.values())
    return summary


def merge_timepoints_to_ranges(
    timepoints: List[str],
    gap_threshold_sec: int = 25,
    pre_roll_sec: int = 8,
    post_roll_sec: int = 15,
    min_duration_sec: int = 20,
    max_duration_sec: int = 120,
    max_ranges: int = 3,
) -> List[dict]:
    secs = []
    for t in timepoints or []:
        s = mss_to_seconds(t)
        if s is not None:
            secs.append(s)

    if not secs:
        return []

    secs = sorted(set(secs))

    clusters = []
    cur = [secs[0]]
    for s in secs[1:]:
        if s - cur[-1] <= gap_threshold_sec:
            cur.append(s)
        else:
            clusters.append(cur)
            cur = [s]
    clusters.append(cur)

    ranges = []
    for c in clusters:
        start = c[0] - pre_roll_sec
        end = c[-1] + post_roll_sec

        if end - start < min_duration_sec:
            need = min_duration_sec - (end - start)
            end += need

        if end - start > max_duration_sec:
            t0 = start
            while t0 < end:
                t1 = min(end, t0 + max_duration_sec)
                ranges.append({"start": seconds_to_mss(t0), "end": seconds_to_mss(t1)})
                t0 = t1
        else:
            ranges.append({"start": seconds_to_mss(start), "end": seconds_to_mss(end)})

    def to_pair(r):
        return (mss_to_seconds(r["start"]) or 0, mss_to_seconds(r["end"]) or 0)

    ranges_sorted = sorted(ranges, key=lambda r: to_pair(r)[0])
    merged = []
    for r in ranges_sorted:
        s, e = to_pair(r)
        if not merged:
            merged.append([s, e])
            continue
        ps, pe = merged[-1]
        if s <= pe:
            merged[-1][1] = max(pe, e)
        else:
            merged.append([s, e])

    merged = [{"start": seconds_to_mss(s), "end": seconds_to_mss(e)} for s, e in merged]
    return merged[:max_ranges]


def postprocess_evidence_ranges(summary: dict) -> dict:
    args = summary.get("arguments") or []
    if not isinstance(args, list):
        return summary

    for a in args:
        if not isinstance(a, dict):
            continue
        tps = a.get("evidence_timestamps") or []
        if isinstance(tps, str):
            tps = [tps]
        if not isinstance(tps, list):
            tps = []

        ranges = merge_timepoints_to_ranges(
            timepoints=[str(x).strip() for x in tps if str(x).strip()],
            gap_threshold_sec=25,
            pre_roll_sec=8,
            post_roll_sec=15,
            min_duration_sec=20,
            max_duration_sec=120,
            max_ranges=3,
        )

        a["evidence_ranges"] = ranges
        a["evidence_ui"] = [f'{r["start"]}–{r["end"]}' for r in ranges]

    return summary