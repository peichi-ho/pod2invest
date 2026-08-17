# apps/summaries/services/sentiment_score.py
"""
個股專屬 risk_score / macro_score 分類（試算計算機用）。

設計依據（已用真實資料驗證過）：
  - 綁定「個股：[名稱]」段落，不用整集籠統的總體經濟環境/風險提示當唯一來源。
  - 「個股：[名稱]」段落需先過 resolve_ticker() grounding，過濾掉非真實股票
    （例如摘要曾把保健品「NAC」誤套用「看多」字眼）。
  - 多股票合併的段落（如「個股：國泰金、富邦金」）直接跳過，避免張冠李戴。
  - 輸入分層：個股段落(必要錨點) + 總體經濟環境(共用背景) +
    操作策略與建議/風險提示(僅在文字裡有點名該股票才納入)。
  - 已知立場標籤（看多/看空/觀望，來自既有摘要pipeline的 podcaster_stance 用語）
    當強信號注入 prompt，優先於模型自行判斷。
"""
import json
import math
from datetime import timedelta
from typing import Optional

import yfinance as yf

from .backtesting import resolve_ticker
from .gemini import gemini_generate_with_retry, sanitize_json_text

MULTI_NAME_SEPARATORS = ["、", "/", "與", "和", ",", "，"]


def is_multi_name_topic(asset_name: str) -> bool:
    return any(sep in asset_name for sep in MULTI_NAME_SEPARATORS)


def extract_explicit_stance(position_text: str) -> Optional[str]:
    """從摘要步驟已產生的 position 文字開頭，抓明確立場標籤（看多/看空/觀望）。"""
    text = (position_text or "").strip()
    prefix = text[:6]
    if "看多" in prefix:
        return "看多"
    if "看空" in prefix:
        return "看空"
    if "觀望" in prefix:
        return "觀望"
    return None


def get_base_vol_asof(ticker: str, asof_date, lookback_days: int = 252) -> Optional[tuple]:
    """算某支股票在 asof_date 當時的『過去一年』base(報酬率)與annual_vol(波動率)。"""
    try:
        t = yf.Ticker(ticker)
        start = asof_date - timedelta(days=lookback_days * 1.5)
        end = asof_date + timedelta(days=1)
        hist = t.history(start=start.date().isoformat(), end=end.date().isoformat(), auto_adjust=True)
        if hist.empty or len(hist) < 100:
            return None
        closes = hist["Close"]
        daily_ret = closes.pct_change().dropna()
        base = (closes.iloc[-1] / closes.iloc[0]) - 1.0
        annual_vol = daily_ret.std() * math.sqrt(252)
        if annual_vol <= 0 or not math.isfinite(base):
            return None
        return float(base), float(annual_vol)
    except Exception:
        return None


def find_grounded_stock_topics(record) -> list[dict]:
    """
    從 record.arguments 找出所有「個股：[名稱]」段落，過濾掉多股票合併的、
    以及 resolve_ticker 找不到真實代號的，回傳 [{"asset_name":..., "ticker":..., "topic":{...}}, ...]
    """
    results = []
    for a in record.arguments or []:
        if not isinstance(a, dict):
            continue
        topic = a.get("topic", "")
        if not topic.startswith("個股："):
            continue
        asset_name = topic[len("個股："):]
        if is_multi_name_topic(asset_name):
            continue
        ticker = resolve_ticker(asset_name)
        if not ticker:
            continue
        results.append({"asset_name": asset_name, "ticker": ticker, "topic": a})
    return results


def find_stock_topics_with_fallback(record) -> list[dict]:
    """
    先用 find_grounded_stock_topics()（嚴格版：獨立成段的「個股：X」，判斷依據完整）；
    段落沒涵蓋到的股票，退而求其次用 outlook_calls 裡對應的那句話當備援內容
    （寬鬆版：只有一句 thesis/evidence_quote，判斷依據比較薄，但至少有得算，
    不會讓使用者點試算卻找不到任何依據）。

    回傳格式跟 find_grounded_stock_topics 相容：[{"asset_name","ticker","topic":{...}}, ...]，
    topic 是組出來、跟 arguments 段落同樣形狀的 dict（有 topic/summary/position 三個 key），
    讓 build_classification_context() 不用另外改就能吃。
    """
    grounded = find_grounded_stock_topics(record)
    covered = {t["ticker"] for t in grounded}

    results = list(grounded)
    for call in (record.outlook_calls or []):
        if not isinstance(call, dict):
            continue
        asset_name = (call.get("asset") or "").strip()
        if not asset_name or is_multi_name_topic(asset_name):
            continue
        ticker = resolve_ticker(asset_name)
        if not ticker or ticker in covered:
            continue

        thesis = (call.get("thesis") or "").strip()
        quote = (call.get("evidence_quote") or "").strip()
        if not thesis and not quote:
            continue

        # outlook_call 有結構化的 direction，比 arguments 段落靠 open text 前綴猜立場更可靠，
        # 組成 extract_explicit_stance() 認得的格式（"看多："/"看空：" 開頭）直接重用既有機制。
        stance = {"bullish": "看多", "bearish": "看空"}.get(call.get("direction", ""), "")
        position_text = f"{stance}：{quote}" if stance else quote

        covered.add(ticker)
        results.append({
            "asset_name": asset_name,
            "ticker": ticker,
            "topic": {
                "topic": f"個股：{asset_name}",
                "summary": thesis,
                "position": position_text,
            },
        })
    return results


def build_classification_context(record, asset_name: str, ticker: str, stock_topic: dict) -> tuple[str, str]:
    """組出餵給 Gemini 的輸入段落 + 已知立場標籤提示。"""
    macro_topic = None
    strategy_topics = []
    for a in record.arguments or []:
        if not isinstance(a, dict):
            continue
        topic = a.get("topic", "")
        if topic == "總體經濟環境":
            macro_topic = a
        elif topic in ("操作策略與建議", "風險提示"):
            blob = json.dumps(a, ensure_ascii=False)
            if asset_name in blob or ticker in blob:
                strategy_topics.append(a)

    parts = [f"【個股：{asset_name}】\n{stock_topic.get('summary','')}\n{stock_topic.get('position','')}"]
    if macro_topic:
        parts.append(f"【總體經濟環境（共用背景）】\n{macro_topic.get('summary','')}")
    for t in strategy_topics:
        parts.append(f"【{t.get('topic')}（有點名此股票）】\n{t.get('summary','')}\n{t.get('position','')}")

    context_text = "\n\n".join(parts)
    stance = extract_explicit_stance(stock_topic.get("position", ""))
    stance_hint = f"\n【已知立場標籤】{stance}\n" if stance else "\n"
    return context_text, stance_hint


RUBRIC_PROMPT_TEMPLATE = """你是一個投資 Podcast 內容分析員，任務是針對「單一股票」在這集節目中的討論，
判斷兩個獨立的分數：macro_score（方向感）與 risk_score（不確定性強度）。

【重要】這兩個分數只針對「{asset_name}（{ticker}）」這支股票，不要被段落裡提到的其他股票影響。

【輸入段落】
以下是這集節目裡，關於這支股票的相關摘要段落（可能包含個股討論、共用總經背景、
以及有明確點名這支股票的操作建議/風險提示）：

{context_text}
{stance_hint}
【macro_score 判定標準（方向感，三選一）】
+1 樂觀：對這支股票未來表現整體呈現正面評價，沒有實質保留意見，或正面論述明顯壓過保留意見
 0 中性：明確標示「觀望」；或看法混合（如短期看多、長期觀望）；或利多利空論點份量相當，沒有明確傾向
-1 悲觀：整體呈現負面評價，看空、警示下行風險是主要論述

【risk_score 判定標準（不確定性強度，跟方向感獨立，三選一）】
0   低：沒有明確提及具體的不確定性、財務壓力、競爭威脅等疑慮，純粹陳述動能或利多
0.5 中：提及至少一項具體風險因子，但只是附帶提醒，不是論述重點；或風險存在但影響範圍/機率不明確
1   高：風險/不確定性是論述重點之一，出現具體數字（如負債金額）、明確競爭威脅、供需疑慮、估值過高，
     或語氣明確強調投資人應謹慎/持續追蹤

【校準範例】
範例A（Rocket Lab）：「...預期能為 Rocket Lab 帶來顯著的營收成長...然而，交易中包含的36億美元橋接貸款
將轉為長期債務，加上Rocket Lab本身仍在開發下一代火箭，大規模投資和利息支出可能帶來財務壓力。」
→ macro_score=1, risk_score=1（收購利多為主軸，但36億美元具體負債數字被明確強調為財務壓力）

範例B（美光）：「短期內受惠於強勁需求和樂觀財測，但中期面臨競爭加劇的壓力，長期需觀察其資本報酬率和供需變化。」
→ macro_score=0, risk_score=0.5（短多中觀望長觀察，時間軸上看法不一致；提到具體競爭對手但無災難性數字）

【已知立場標籤的處理原則】
如果【輸入段落】裡有出現「已知立場標籤」，這是原始摘要員對這支股票的立場判斷，
你的 macro_score 判定應該以這個標籤為強信號、優先採用：
  看多 → macro_score 應為 1（除非段落內容本身有明顯自相矛盾，讓「看多」站不住腳）
  看空 → macro_score 應為 -1
  觀望 → macro_score 應為 0
不要因為段落裡提到風險/保留意見，就自行把「看多」標籤降級成中性——
風險/保留意見應該反映在 risk_score，不是拿去覆蓋已經明確給出的方向判斷。

只能輸出合法 JSON，不可包含任何解釋文字或 markdown：
{{"macro_score": <-1/0/1的其中一個數字>, "risk_score": <0/0.5/1的其中一個數字>,
 "rationale": "一句話說明理由，20字以內"}}
"""


def classify_stock_sentiment(client, model: str, record, asset_name: str, ticker: str, stock_topic: dict) -> Optional[dict]:
    """呼叫 Gemini，回傳 {"macro_score":..., "risk_score":..., "rationale":...}，失敗回傳 None。"""
    context_text, stance_hint = build_classification_context(record, asset_name, ticker, stock_topic)
    prompt = RUBRIC_PROMPT_TEMPLATE.format(
        asset_name=asset_name, ticker=ticker, context_text=context_text, stance_hint=stance_hint,
    )
    try:
        resp = gemini_generate_with_retry(
            client=client, model=model, prompt_text=prompt,
            temperature=0.0, max_output_tokens=300, max_tries=3,
        )
        obj = json.loads(sanitize_json_text(getattr(resp, "text", "") or ""))
        return {
            "macro_score": float(obj["macro_score"]),
            "risk_score": float(obj["risk_score"]),
            "rationale": str(obj.get("rationale", ""))[:200],
        }
    except Exception:
        return None


def ensure_stock_sentiment_score(record, ticker: str, client=None, model: Optional[str] = None):
    """
    給定一筆 SummaryRecord 跟目標 ticker，確保這支股票有 StockSentimentScore 可用：
    已經算過就直接回傳既有那筆；沒算過就當場分類、存進資料庫再回傳（cache-aside，
    跟 backfill_stock_sentiment management command 共用同一套邏輯，避免兩邊邏輯兜不起來）。

    用 find_stock_topics_with_fallback()：優先用「個股：X」段落（判斷依據完整），
    沒有的話退而求其次用 outlook_calls 的那句話（判斷依據較薄，但至少有得算）。
    兩者都沒有才回傳 None——這種情況才是這集真的完全沒提到這支股票，沒東西可算。
    """
    import os
    from apps.summaries.models import StockSentimentScore
    from .gemini import make_client

    if not record.published_at:
        return None

    topics = find_stock_topics_with_fallback(record)
    match = next((t for t in topics if t["ticker"] == ticker), None)
    if not match:
        return None

    asset_name = match["asset_name"]
    existing = (
        StockSentimentScore.objects.using("summariesdb")
        .filter(summary=record, asset_name=asset_name)
        .first()
    )
    if existing:
        return existing

    bv = get_base_vol_asof(ticker, record.published_at)
    if not bv:
        return None
    base, annual_vol = bv

    if client is None:
        client = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
    if model is None:
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    result = classify_stock_sentiment(client, model, record, asset_name, ticker, match["topic"])
    if not result:
        return None

    return StockSentimentScore.objects.using("summariesdb").create(
        summary=record,
        episode_id=record.episode_id,
        asset_name=asset_name,
        ticker=ticker,
        base=base,
        annual_vol=annual_vol,
        macro_score=result["macro_score"],
        risk_score=result["risk_score"],
        rationale=result["rationale"],
    )
