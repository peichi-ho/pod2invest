# apps/knowledge_graph/services/basket_selection.py
"""
統一 Selection 層：把 Supply / Substitute / Co-impact 三策略產生的候選名單，
收斂成最終的投資組合 basket。

流程：
  1. 節點類型過濾：排除不是真實公司/標的的節點（如「AI」「半導體」這種概念/產業節點）
  2. 依分數排序，先截取前 top_n_prefilter 名，控制後續 LLM 呼叫成本
  3. Reason 品質過濾：排除只有模糊、分類式描述的候選
  4. （僅 Supply）方向判斷：LLM 判斷 reason 文字是否真的支持 source→target 的方向
     （僅 Substitute）關係判斷：LLM 判斷 reason 文字是否真的在講競爭/替代關係
  5. Basket 大小上限
  6. （選填）Layer 2 財務體質篩選：依風險分級套用 F-score 門檻

只對「hops == 1」（跟 seed 直接相連、有具體 reason 可查）的候選做 reason 品質 /
方向判斷；多跳候選沒有單一 edge 可以引用，交給 PPR 分數本身反映的距離衰減。
"""
from __future__ import annotations

import difflib
import re

from django.db import connections

from apps.summaries.services.backtesting import resolve_ticker
from apps.knowledge_graph.generate import _call_gemini_with_retry


def seed_exists(seed: str) -> bool:
    with connections["knowledge_graphdb"].cursor() as cursor:
        cursor.execute("SELECT 1 FROM nodes WHERE name = %s", [seed])
        return cursor.fetchone() is not None


def suggest_similar_seeds(seed: str, limit: int = 5) -> list[str]:
    """
    seed 在圖譜裡查無此節點時，用模糊比對從既有節點名稱裡找相近的建議。
    節點數量不大（幾千筆等級），一次抓全部在 Python 端比對即可，不需要
    額外的資料庫模糊搜尋擴充套件。
    """
    with connections["knowledge_graphdb"].cursor() as cursor:
        cursor.execute("SELECT name FROM nodes")
        all_names = [row[0] for row in cursor.fetchall()]
    return difflib.get_close_matches(seed, all_names, n=limit, cutoff=0.4)

# resolve_ticker() 底層的 _rule_based_ticker() 對「純大寫英文字母、最多6碼」的名稱會
# 直接原樣放行當作合法 ticker，不驗證是否真的存在（例如 "CPU"、"ASIC" 這類技術名詞
# 會被誤判）。對這個形狀的名稱額外做一次 yfinance 存在性檢查來擋掉。
_TICKER_SHAPE_RE = re.compile(r"^[A-Z]{1,6}$")

# "AI" 是已驗證過的例外：它剛好對應到真實存在、有交易的美股代號 C3.ai（NYSE: AI），
# yfinance 存在性檢查也會通過，但在 podcast 語境下幾乎必然指的是「AI 熱潮」這個概念，
# 不是這家公司，因此需要明確排除。
_VERIFIED_FALSE_POSITIVE_NODES = {"AI"}

# resolve_ticker() 底層的關鍵字規則（INDEX_MAP/COMMODITY_MAP）會把「那斯達克」
# 「黃金」這類巨觀名詞解析成指數/商品/外匯代號（例如 "^IXIC"、"GC=F"、"JPY=X"），
# 這是為了給其他地方（總經commentary回測）用的，但在這裡代表的是一個真實可投資
# 的「公司」，指數/商品/外匯/加密貨幣都不是公司，不該進 basket。yfinance 這幾類
# 代號有固定格式可以辨識，用來排除。
_NON_COMPANY_TICKER_RE = re.compile(r"^\^|=F$|=X$")
_KNOWN_NON_COMPANY_TICKERS = {"BTC-USD"}


def _is_non_company_ticker(ticker: str) -> bool:
    return bool(_NON_COMPANY_TICKER_RE.search(ticker)) or ticker in _KNOWN_NON_COMPANY_TICKERS


def _verify_ticker_live(ticker: str) -> bool:
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d")
        return not hist.empty
    except Exception:
        return False


_VAGUE_REASON_PATTERNS = [
    re.compile(r"都.{0,4}(受到|受惠|受害|影響)"),
    re.compile(r"(屬於|隸屬於).{0,10}(產業|行業|類股)"),
    re.compile(r"都是.{0,10}(公司|產業|類股)"),
    re.compile(r"都被.{0,6}(提及|談到|討論)"),
]


# ── 1. 節點類型過濾 ──────────────────────────────────────────────────────

def filter_node_type(candidates: list[dict]) -> list[dict]:
    kept = []
    for c in candidates:
        name = c["node"]
        if name in _VERIFIED_FALSE_POSITIVE_NODES:
            continue
        try:
            ticker = resolve_ticker(name)
        except Exception:
            ticker = ""
        if not ticker:
            continue
        if _is_non_company_ticker(ticker):
            continue
        if _TICKER_SHAPE_RE.match(name) and not _verify_ticker_live(ticker):
            continue
        kept.append(c)
    return kept


# ── 2. Reason 品質過濾 ───────────────────────────────────────────────────

def _is_vague_reason(reason: str) -> bool:
    return any(p.search(reason) for p in _VAGUE_REASON_PATTERNS)


def filter_reason_quality(candidates: list[dict]) -> list[dict]:
    kept = []
    for c in candidates:
        reasons = c.get("reasons", [])
        if not reasons:
            # 多跳候選沒有單一 reason 可查，交給 PPR 分數衰減處理，直接放行
            kept.append(c)
            continue
        if any(not _is_vague_reason(r) for r in reasons):
            kept.append(c)
        # 全部 reason 都模糊 → 排除
    return kept


# ── 3. Supply 方向判斷（LLM） ─────────────────────────────────────────────

def _llm_check_direction(reason: str, expected_supplier: str, expected_customer: str) -> bool:
    prompt = (
        f"這段描述在講一個供應鏈關係：\n「{reason}」\n\n"
        f"請判斷：這段描述是否支持「{expected_supplier} 供應/提供產品或服務給 {expected_customer}」這個方向？\n"
        "只回答 YES 或 NO，不要解釋。"
    )
    try:
        return _call_gemini_with_retry(prompt).strip().upper().startswith("YES")
    except Exception:
        # 判斷失敗時保守起見不排除，交給其他過濾條件處理
        return True


def check_supply_direction(candidates: list[dict], seed: str, direction: str) -> list[dict]:
    """
    direction: "upstream"（每一段代表 to 供應 from）或 "downstream"（每一段代表 from 供應 to）

    驗證整條 path 的每一段，不是只驗證跟 seed 直接相連的那一段——否則像
    「seed → A →（方向記反的邊）→ B」這種案例，B 完全沒被驗證過方向就會
    漏進 basket。任一段有 reason 但驗證不支持方向，整個候選排除；沒有
    reason 的段落無法驗證，不計入失敗，避免多跳候選被過度懲罰。
    """
    kept = []
    for c in candidates:
        segments = c.get("path_reasons", [])
        if not segments:
            kept.append(c)
            continue

        all_pass = True
        for seg in segments:
            seg_reasons = seg.get("reasons", [])
            if not seg_reasons:
                continue  # 無法驗證，不計入失敗
            reason = seg_reasons[0]

            a, b = seg["from"], seg["to"]
            if direction == "upstream":
                expected_supplier, expected_customer = b, a
            else:
                expected_supplier, expected_customer = a, b

            if not _llm_check_direction(reason, expected_supplier, expected_customer):
                all_pass = False
                break

        if all_pass:
            kept.append(c)

    return kept


# ── 3c. Substitute 關係品質判斷（LLM） ────────────────────────────────────

def _llm_check_substitution(reason: str, node_a: str, node_b: str) -> bool:
    prompt = (
        f"這段描述在講兩家公司的關係：\n「{reason}」\n\n"
        f"請判斷：這段描述是否支持「{node_a}」與「{node_b}」是市場上互相競爭、"
        "可以互相替代的關係？（不是供應鏈上下游、合作夥伴，也不是跟這兩家公司"
        "業務關聯不大的其他敘述，例如選擇權操作策略、財經知識講解）\n"
        "只回答 YES 或 NO，不要解釋。"
    )
    try:
        return _call_gemini_with_retry(prompt).strip().upper().startswith("YES")
    except Exception:
        # 判斷失敗時保守起見不排除，交給其他過濾條件處理
        return True


def check_substitution_relevance(candidates: list[dict], seed: str) -> list[dict]:
    """
    relation_type 標成 Substitution 不保證內容真的在講競爭/替代關係——實測發現
    LLM 抽取階段會把供應鏈敘述（例如「Intel 搶走部分台積電的蘋果訂單」）或甚至
    跟公司關係無關的內容（選擇權操作策略）誤標成 Substitution。這裡用 LLM 針對
    每個候選的 reason 文字重新判斷一次，過濾掉標籤跟內容對不上的候選。
    """
    kept = []
    for c in candidates:
        reasons = c.get("reasons", [])
        if not reasons:
            kept.append(c)
            continue
        if _llm_check_substitution(reasons[0], seed, c["node"]):
            kept.append(c)
    return kept


# ── 3b. 整合 reason（取代「隨機抽第一筆」）──────────────────────────────────

def synthesize_reason(candidate: dict, seed: str, window_days: int) -> str:
    """
    候選路徑上每一段可能各自累積了好幾筆不同集數的原始 reason，過去做法是
    直接顯示 list 裡第一筆（順序未排序，等於隨機挑一句）。這裡改成把整條
    路徑每一段的完整 reason 清單，連同 mention_count/source_diversity 這些
    佐證強度資訊，一起交給 LLM 整合成一句連貫、具體的說明。

    只在候選已經進入最終 basket（數量很少）之後才呼叫，不會對大量候選
    逐一呼叫 LLM。

    window_days：查詢用的圖譜回看窗口（使用者在前端選的7/14/30天），不能寫死
    90——mention_note 講的是「這段關係在最近『這個窗口』被提到幾次」，窗口本身
    是使用者選的，寫死跟實際查詢範圍會對不起來。
    """
    path = candidate.get("path") or [seed, candidate["node"]]
    segments = candidate.get("path_reasons", [])

    segment_texts = []
    for seg in segments:
        seg_reasons = seg.get("reasons", [])
        if seg_reasons:
            joined = "；".join(seg_reasons[:8])
            segment_texts.append(f"{seg['from']}→{seg['to']}：{joined}")

    if not segment_texts:
        return f"{' → '.join(path)}（圖譜路徑相連，但查無具體描述）"

    mention_note = ""
    if candidate.get("hops") == 1 and candidate.get("mention_count"):
        mention_note = (
            f"（這段關係在過去{window_days}天內被 {candidate.get('source_diversity', 0)} "
            f"個不同節目、共 {candidate['mention_count']} 次提及）"
        )

    prompt = (
        f"以下是從「{seed}」到「{candidate['node']}」的知識圖譜路徑，"
        "以及每一段關係在 podcast 中被提及的原始描述（同一段可能有多筆，"
        "是不同集數各自的說法，內容可能重複、角度略有不同，也可能包含當時"
        "討論的背景或觸發事件）：\n\n"
        f"路徑：{' → '.join(path)}\n\n"
        + "\n".join(segment_texts)
        + f"\n{mention_note}\n\n"
        "請把這些描述整合成一句通順、具體的繁體中文說明（不超過80字），"
        "重點是要讓讀者看完知道「這兩者最近為什麼會被放在一起討論」——"
        "如果原始描述裡有提到具體的觸發事件、新聞、產業動態，優先納入，"
        "不要只重複「A供應B」這種靜態關係描述而遺漏了時效性的背景；如果原始"
        "描述真的只有靜態關係、沒有提到任何觸發背景，才單純說明這條路徑代表"
        "的商業關係是什麼。多筆描述講同一件事就合併成一個重點，不要條列；"
        "如果不同筆描述講的是不同面向，可以簡短涵蓋主要1-2點。"
        "只輸出整合後的句子，不要加任何前綴、解釋或引號。"
    )
    try:
        result = _call_gemini_with_retry(prompt).strip()
        if result:
            return result
    except Exception:
        pass
    # LLM 失敗時退回顯示第一段的第一筆原始 reason，不讓候選完全沒有說明
    first_reasons = segments[0].get("reasons", []) if segments else []
    return first_reasons[0] if first_reasons else " → ".join(path)


# ── 5. 產業標籤 ──────────────────────────────────────────────────────────

def _get_industries(node_names: list[str]) -> dict[str, str]:
    if not node_names:
        return {}
    with connections["knowledge_graphdb"].cursor() as cursor:
        cursor.execute(
            "SELECT name, industry FROM nodes WHERE name = ANY(%s)",
            [node_names],
        )
        return {row[0]: (row[1] or "其他") for row in cursor.fetchall()}


# ── 6. 主流程 ─────────────────────────────────────────────────────────────

def select_basket(
    candidates: list[dict],
    *,
    strategy: str,  # "supply_upstream" | "supply_downstream" | "substitute" | "co_impact"
    seed: str,
    window_days: int = 30,
    top_n_prefilter: int = 30,
    max_basket_size: int = 8,
    risk_tier: str | None = None,
    as_of_date=None,
) -> dict:
    """
    回傳 {"basket": [...], "funnel": [...]}。
    funnel 記錄每一關過濾前後的候選數，讓使用者看得到「623 個候選怎麼變成 2 個」，
    不是黑盒結果；relative_strength（0-100）則是把候選池裡最高分正規化成 100，
    取代對使用者沒有意義的原始 PPR 浮點數分數。
    """
    funnel = [{"stage": "候選產生（PPR / 1-hop / 社群偵測）", "count": len(candidates)}]

    if not candidates:
        return {"basket": [], "funnel": funnel}

    max_score = max(c["score"] for c in candidates) or 1

    candidates = filter_node_type(candidates)
    funnel.append({"stage": "節點類型過濾（排除非真實標的）", "count": len(candidates)})

    candidates = sorted(candidates, key=lambda c: -c["score"])[:top_n_prefilter]
    funnel.append({"stage": f"取分數前 {top_n_prefilter} 名", "count": len(candidates)})

    candidates = filter_reason_quality(candidates)
    funnel.append({"stage": "Reason 品質過濾", "count": len(candidates)})

    if strategy in ("supply_upstream", "supply_downstream"):
        direction = "upstream" if strategy == "supply_upstream" else "downstream"
        candidates = check_supply_direction(candidates, seed, direction)
        funnel.append({"stage": "供應方向 AI 判斷", "count": len(candidates)})
    elif strategy == "substitute":
        candidates = check_substitution_relevance(candidates, seed)
        funnel.append({"stage": "替代關係 AI 判斷", "count": len(candidates)})

    candidates = sorted(candidates, key=lambda c: -c["score"])[:max_basket_size]
    funnel.append({"stage": "Basket 大小上限", "count": len(candidates)})

    if candidates:
        industries = _get_industries([c["node"] for c in candidates])
        for c in candidates:
            c["industry"] = industries.get(c["node"], "其他")

    for c in candidates:
        # reasons 前端會用「展開」收合，不用強壓在 5 條，但 mention_count
        # 高的候選可能累積上百條，還是要有上限避免回應過大。
        if c.get("reasons"):
            c["reasons"] = c["reasons"][:10]
        c["relative_strength"] = round(100 * c["score"] / max_score)
        # 只對最終進入 basket 的少數候選做整合，不是對大量候選逐一呼叫 LLM。
        c["synthesized_reason"] = synthesize_reason(c, seed, window_days)

    if risk_tier and candidates:
        import datetime as _dt
        from .fundamental_score import annotate_basket_with_scores

        candidates = annotate_basket_with_scores(
            candidates, risk_tier, as_of_date or _dt.date.today()
        )
        funnel.append({"stage": f"財務體質篩選（{risk_tier}）", "count": len(candidates)})

    return {"basket": candidates, "funnel": funnel}
