# apps/summaries/services/postprocess.py
import re
from typing import List, Optional

from .srt import mss_to_seconds, seconds_to_mss
from .tag_taxonomy import (
    empty_classification,
    normalize_classification_dict,
    flatten_classification_to_tags,
    dedupe_str_list,
)


def _has_numeric_value(value: str) -> bool:
    """
    value 欄位裡是否含有數量性內容。
    接受：阿拉伯數字（59.5%）、中文數字作為量詞使用（二十幾億、十幾%、三個月漲一倍）。
    拒絕：純描述文字（明年、高、懸而未發）、序數（第四季、第一季）。
    """
    if re.search(r"\d", value):
        return True
    # 中文數字後面緊跟量詞/單位，才算是有效數值
    # 例：二十幾億、十幾%、三個月、四百多億、兩倍
    return bool(re.search(
        r"[零一二三四五六七八九十百千萬億兆两兩]"
        r"[多幾餘余]?"
        r"[億萬元%％個月倍美台幾年成折點]",
        value,
    ))


def _normalize_evidence_timestamps(values):
    out = []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []

    for v in values:
        s = str(v).strip()
        if not s:
            continue

        matches = re.findall(r"\d+:\d{2}", s)

        for m in matches:
            if m not in out:
                out.append(m)

    return out


_MACRO_LEVELS = {"大幅樂觀", "樂觀", "中性", "悲觀", "大幅悲觀"}
_RISK_LEVELS = {"低", "中", "高"}


def _normalize_episode_macro(value) -> dict:
    if not isinstance(value, dict):
        return {"level": "中性", "reason": ""}
    level = value.get("level") if value.get("level") in _MACRO_LEVELS else "中性"
    return {"level": level, "reason": (value.get("reason") or "").strip()}


def _has_risk_section(arguments: list) -> bool:
    return any(
        isinstance(a, dict)
        and a.get("topic") == "風險提示"
        and (a.get("summary") or "").strip()
        for a in arguments
    )


def _normalize_episode_risk(value: dict, arguments: list) -> dict:
    """
    沒有「風險提示」段落時，不管 LLM 輸出什麼，一律覆寫成低風險——
    避免在完全沒有風險相關內容的情況下，還讓 LLM 憑空給出中/高的判斷。
    """
    if not _has_risk_section(arguments):
        return {"level": "低", "reason": "本集無風險提示段落"}

    if not isinstance(value, dict):
        return {"level": "中", "reason": ""}
    level = value.get("level") if value.get("level") in ("中", "高") else "中"
    return {"level": level, "reason": (value.get("reason") or "").strip()}


def normalize_schema(summary: dict) -> dict:
    if not isinstance(summary, dict):
        return {}

    summary.setdefault("one_sentence_summary", "")
    summary.setdefault("investment_takeaways", {})
    summary["investment_takeaways"].setdefault("bullish", [])
    summary["investment_takeaways"].setdefault("bearish", [])
    summary["investment_takeaways"].setdefault("watchlist", [])
    summary["investment_takeaways"].setdefault("podcaster_stance", "混合/視情況")

    summary.setdefault("classification", empty_classification())
    summary["classification"] = normalize_classification_dict(
        summary.get("classification", {})
    )

    # tags 保留做向下相容，但不再由 LLM 自由生成
    summary["tags"] = flatten_classification_to_tags(summary["classification"])

    summary.setdefault("entities", {})
    summary["entities"].setdefault("companies_or_stocks", [])
    summary["entities"].setdefault("countries_or_regions", [])
    summary["entities"].setdefault("people", [])

    summary.setdefault("arguments", [])
    summary.setdefault("outlook_calls", [])

    summary["entities"]["companies_or_stocks"] = dedupe_str_list(
        summary["entities"].get("companies_or_stocks", [])
    )
    summary["entities"]["countries_or_regions"] = dedupe_str_list(
        summary["entities"].get("countries_or_regions", [])
    )
    summary["entities"]["people"] = dedupe_str_list(
        summary["entities"].get("people", [])
    )

    args = summary.get("arguments") or []
    if isinstance(args, list):
        for a in args:
            if not isinstance(a, dict):
                continue

            a.setdefault("topic", "")
            a.setdefault("position", "")
            a.setdefault("summary", "")
            a.setdefault("key_data", [])
            a.setdefault("related_concepts", [])
            a.setdefault("evidence_timestamps", [])

            a["evidence_timestamps"] = _normalize_evidence_timestamps(
                a.get("evidence_timestamps", [])
            )

            a["related_concepts"] = dedupe_str_list(a.get("related_concepts", []))

            kd = a.get("key_data", [])

            if isinstance(kd, dict):
                new_list = []
                for k, v in kd.items():
                    if v is None:
                        continue
                    new_list.append({
                        "label": str(k).strip(),
                        "value": str(v).strip(),
                        "context": "",
                    })
                a["key_data"] = new_list

            elif isinstance(kd, str):
                s = kd.strip()
                a["key_data"] = [{"label": "", "value": s, "context": ""}] if s else []

            elif isinstance(kd, list):
                new_kd = []
                for row in kd:
                    if isinstance(row, dict):
                        new_kd.append({
                            "label": str(row.get("label", "")).strip(),
                            "value": str(row.get("value", "")).strip(),
                            "context": str(row.get("context", "")).strip(),
                        })
                    elif isinstance(row, str):
                        s = row.strip()
                        if s:
                            new_kd.append({
                                "label": "",
                                "value": s,
                                "context": "",
                            })
                a["key_data"] = new_kd

            else:
                a["key_data"] = []

            # value 不含數字的條目視為無效（如「明年」「懸而未發」「高」），直接捨棄
            a["key_data"] = [
                kd for kd in a["key_data"]
                if _has_numeric_value(kd.get("value", ""))
            ]

        # ── Topic 正規化：把 LLM 可能產生的變體統一成標準格式 ──────────────────
        def _normalize_topic(t: str) -> str:
            t = t.strip()
            # 固定類型：前綴或包含關鍵字就收攏
            if t.startswith("總體經濟環境"):
                return "總體經濟環境"
            if "操作策略" in t:
                return "操作策略與建議"
            if t.startswith("風險提示"):
                return "風險提示"
            # 個股型：「個股」後面接任意字元再接冒號（全形/半形皆可）
            m = re.match(r"^個股.*?[：:](.+)$", t)
            if m:
                return f"個股：{m.group(1).strip()}"
            # 產業型：「產業」後面接任意字元再接冒號
            m = re.match(r"^產業.*?[：:](.+)$", t)
            if m:
                return f"產業：{m.group(1).strip()}"
            return t

        for a in args:
            if isinstance(a, dict):
                a["topic"] = _normalize_topic((a.get("topic") or "").strip())

        # 合併相同 topic 的 arguments
        merged_map: dict = {}
        merged_order: list = []
        for a in args:
            if not isinstance(a, dict):
                continue
            topic = (a.get("topic") or "").strip()
            if topic not in merged_map:
                merged_map[topic] = {
                    "topic": topic,
                    "position": (a.get("position") or "").strip(),
                    "summary": (a.get("summary") or "").strip(),
                    "key_data": list(a.get("key_data") or []),
                    "related_concepts": list(a.get("related_concepts") or []),
                    "evidence_timestamps": list(a.get("evidence_timestamps") or []),
                }
                merged_order.append(topic)
            else:
                base = merged_map[topic]
                # position：保留較長的那個
                new_pos = (a.get("position") or "").strip()
                if len(new_pos) > len(base["position"]):
                    base["position"] = new_pos
                # summary：兩段合併，以換行分隔
                new_sum = (a.get("summary") or "").strip()
                if new_sum and new_sum not in base["summary"]:
                    base["summary"] = base["summary"] + "\n" + new_sum if base["summary"] else new_sum
                # key_data：直接 extend（值層級去重靠 label+value）
                existing_kd_keys = {
                    (kd.get("label", ""), kd.get("value", ""))
                    for kd in base["key_data"]
                }
                for kd in (a.get("key_data") or []):
                    key = (kd.get("label", ""), kd.get("value", ""))
                    if key not in existing_kd_keys:
                        base["key_data"].append(kd)
                        existing_kd_keys.add(key)
                # related_concepts：合併去重
                existing_rc = set(base["related_concepts"])
                for rc in (a.get("related_concepts") or []):
                    if rc not in existing_rc:
                        base["related_concepts"].append(rc)
                        existing_rc.add(rc)
                # evidence_timestamps：合併去重，合併後上限 10 筆
                existing_ts = set(base["evidence_timestamps"])
                for ts in (a.get("evidence_timestamps") or []):
                    if ts not in existing_ts:
                        base["evidence_timestamps"].append(ts)
                        existing_ts.add(ts)

        # 每個 argument 的 evidence_timestamps 上限 10 筆（依時間序保留最早的）
        _MAX_TS = 10
        for base in merged_map.values():
            ts_list = base.get("evidence_timestamps") or []
            if len(ts_list) > _MAX_TS:
                # 依 m:ss 數值排序後取前 _MAX_TS
                def _ts_sec(t: str) -> int:
                    try:
                        parts = t.strip().split(":")
                        return int(parts[0]) * 60 + int(parts[1])
                    except Exception:
                        return 0
                ts_list_sorted = sorted(ts_list, key=_ts_sec)
                # 保留前、中、後的分布：均勻抽樣 _MAX_TS 筆
                step = len(ts_list_sorted) / _MAX_TS
                sampled = [ts_list_sorted[int(i * step)] for i in range(_MAX_TS)]
                base["evidence_timestamps"] = sampled

        merged_args = [merged_map[t] for t in merged_order]

        # 固定排序：總體經濟環境 → 產業：* → 個股：* → 操作策略與建議 → 風險提示 → 其他
        def _arg_sort_key(a: dict) -> tuple:
            topic = (a.get("topic") or "").strip()
            if topic == "總體經濟環境":
                return (0, topic)
            if topic.startswith("產業：") or topic.startswith("產業:"):
                return (1, topic)
            if topic.startswith("個股：") or topic.startswith("個股:"):
                return (2, topic)
            if topic == "操作策略與建議":
                return (3, topic)
            if topic == "風險提示":
                return (4, topic)
            return (5, topic)

        summary["arguments"] = sorted(merged_args, key=_arg_sort_key)

    summary["episode_macro"] = _normalize_episode_macro(summary.get("episode_macro"))
    summary["episode_risk"] = _normalize_episode_risk(
        summary.get("episode_risk"), summary.get("arguments") or []
    )

    oc = summary.get("outlook_calls") or []
    if isinstance(oc, dict):
        summary["outlook_calls"] = [oc]
    elif isinstance(oc, str):
        summary["outlook_calls"] = []
    elif not isinstance(oc, list):
        summary["outlook_calls"] = []

    if not (summary.get("one_sentence_summary") or "").strip():
        args = summary.get("arguments") or []
        if args and isinstance(args, list):
            first_topic = ""
            first_position = ""

            for a in args:
                if isinstance(a, dict):
                    first_topic = (a.get("topic") or "").strip()
                    first_position = (a.get("position") or "").strip()
                    if first_topic:
                        break

            if first_topic and first_position:
                summary["one_sentence_summary"] = f"本集重點聚焦於{first_topic}，整體觀點為{first_position}。"
            elif first_topic:
                summary["one_sentence_summary"] = f"本集重點聚焦於{first_topic}。"

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

    ticker_re = re.compile(r"^\d{3,6}$")
    us_ticker_re = re.compile(r"^[A-Z]{1,5}$")
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
