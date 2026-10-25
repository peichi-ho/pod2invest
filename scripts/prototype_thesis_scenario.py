# scripts/prototype_thesis_scenario.py
"""
小規模原型：把驗證句(BacktestingRecord)拆解成「論點情境」，給之後的「論點情境試算」用
(驗證句 → 需求↑ → 營收↑ → 毛利率↑ → EPS↑ → 股價↑)。

設計原則：AI 只做「定性」——從固定的情境類型清單(SCENARIO_TYPES)裡挑出這句論點
包含哪些情境、每個情境是 Podcast 主張的(podcast) 還是節目提到的風險(risk)、因果鏈
怎麼走、原文依據是哪一句。每個情境「影響哪個財務參數、往哪個方向」由程式碼裡的
SCENARIO_TYPES 對照表決定，不讓 AI 決定；「影響幅度」之後由該公司歷史財報分布給預設值，
這裡完全不碰數字——避免 AI 隨口生出「營收+25%」這種沒有來源的數字。

只抽「有財報資料(FinancialReportCache)」的個股驗證句(pro 版、排除期貨/指數/加密貨幣)，
不寫回任何資料表，結果輸出到 out/(已在 .gitignore)給人工檢查分類準不準。

v1 → v2 的修正(v1 跑30句、108個情境約一半有問題，見 out/thesis_scenario_sample.md)：
  1. 加 TECHNICAL_FLOW(技術面/籌碼面)類別、不對應任何財務參數——v1 把「月線反彈」「外資
     買超」「慣性拉回」都硬塞進 RERATING_UP/DOWN，甚至把「底部有買盤」當成產品需求。
  2. 補充段落不再放「總體經濟環境」整段——v1 的降息/QT/VIX/非農情境幾乎都是從那段共用
     背景滲進來的，跟這家公司無關；總經情境只在節目明確連結到這家公司時才列。
  3. 只列「未來的變化」：現況優勢(高毛利率、市佔率高)、已公布的數字(營收年增62%)、
     「不受某風險影響」都不是情境。
  4. 程式端依(type, stance)去重——v1 同一個驅動因素常常重複列兩三次。
  5. quote 比對前把全形/半形標點、空白都去掉，且 prompt 禁止用省略號拼接原文。

v2 → v3 的修正(v2 剩約27%有問題，prompt 寫了規則模型卻沒照做的，改由程式強制)：
  1. 因果鏈終點是股價就改 TECHNICAL_FLOW；RERATING 的 quote 沒有估值字眼就降級(apply_checks)。
  2. 去重時優先留 quote 對得到原文、沒被程式改過類型的那筆(dedupe_scenarios_v3)。
  3. stance 不讓 AI 判斷：AI 只給 effect(利多/利空)，程式依情境類型的 levers 跟論點方向
     推出 support/against——v2 對看空論點的 stance 判斷不穩(#1 南亞科)。
  4. prompt 補空泛句反例(「長期看好」「僅需關注景氣」不列，不可從中性字眼推出正反兩個情境)。
  5. --max-per-ticker：每檔股票最多抽幾句(v1/v2 的樣本台積電/特斯拉/輝達就佔13句)。

用法：python scripts/prototype_thesis_scenario.py [--version v3] [--max-per-ticker 2] [--n 30] [--seed 42] [--model gemini-2.5-flash]
"""
import argparse
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.summaries.models import BacktestingRecord, FinancialReportCache
from apps.summaries.services.gemini import gemini_generate_with_retry, make_client, sanitize_json_text
from apps.summaries.services.sentiment_score import build_classification_context, find_grounded_stock_topics

# ── 固定的情境類型清單 ─────────────────────────────────────────────────────
# levers：這個情境會動到哪些財務參數、方向(+1/-1)。由程式碼決定，AI 只挑類型。
#   revenue_growth 營收成長率 / gross_margin 毛利率 / opex_ratio 營業費用率 / pe 本益比
# 空的 levers(TECHNICAL_FLOW)代表「節目有講、但不是基本面」，試算時只顯示不計算。
SCENARIO_TYPES = {
    "DEMAND_UP":          {"name": "需求、訂單、出貨增加",       "levers": {"revenue_growth": +1}},
    "NEW_BUSINESS":       {"name": "新產品、新客戶、新市場",     "levers": {"revenue_growth": +1}},
    "PRICE_UP":           {"name": "產品漲價、報價上揚",         "levers": {"revenue_growth": +1, "gross_margin": +1}},
    "MIX_UPGRADE":        {"name": "產品組合升級、高毛利產品佔比提高", "levers": {"gross_margin": +1}},
    "COST_EFFICIENCY":    {"name": "成本下降、良率提升、規模效應", "levers": {"gross_margin": +1, "opex_ratio": -1}},
    "CAPACITY_EXPANSION": {"name": "擴產、新產能開出",           "levers": {"revenue_growth": +1, "gross_margin": -1}},
    "M_AND_A":            {"name": "併購、合併",                 "levers": {"revenue_growth": +1}},
    "DEMAND_DOWN":        {"name": "需求放緩、庫存調整、砍單",   "levers": {"revenue_growth": -1}},
    "COMPETITION":        {"name": "競爭加劇、替代品、市佔流失", "levers": {"revenue_growth": -1, "gross_margin": -1}},
    "COST_UP":            {"name": "成本上升(原物料、匯率、折舊)", "levers": {"gross_margin": -1}},
    "PRICE_DOWN":         {"name": "產品跌價、價格戰",           "levers": {"revenue_growth": -1, "gross_margin": -1}},
    "RERATING_UP":        {"name": "估值修復、超跌、本益比擴張", "levers": {"pe": +1}},
    "RERATING_DOWN":      {"name": "估值過高、本益比收縮",       "levers": {"pe": -1}},
    "MACRO_EASING":       {"name": "降息、資金寬鬆",             "levers": {"pe": +1}},
    "MACRO_TIGHTENING":   {"name": "升息、資金緊縮、總經風險",   "levers": {"pe": -1}},
}

# v2 新增：技術面/籌碼面，不對應財務參數
SCENARIO_TYPES_V2 = {
    **SCENARIO_TYPES,
    "RERATING_UP":   {"name": "估值偏低、本益比低於歷史區間（要有估值依據）", "levers": {"pe": +1}},
    "RERATING_DOWN": {"name": "估值偏高、本益比高於歷史區間（要有估值依據）", "levers": {"pe": -1}},
    "TECHNICAL_FLOW": {"name": "技術面、籌碼面、股價走勢（均線、型態、法人/散戶買賣超、季節性、漲多拉回）", "levers": {}},
}

LEVER_ZH = {"revenue_growth": "營收成長", "gross_margin": "毛利率", "opex_ratio": "費用率", "pe": "P/E"}

NON_STOCK_PATTERN = re.compile(r"=F$|^\^|-USD$")

PROMPT_V1 = """你是一位證券分析師，任務是把 Podcast 節目對「{asset_name}（{ticker}）」的投資論點，
拆解成可以對應到財務模型的「情境」，之後會用來試算 營收 → 毛利 → EPS → 股價。

【驗證句（節目對這檔股票的核心論點）】
方向：{direction}　時間：{timeframe}
論點：{thesis}
原文引述：{evidence_quote}

【補充段落（同一集節目的相關摘要，可能包含總經背景與風險提示）】
{context_text}

【情境類型清單（只能從這裡選，type 填代碼）】
{type_list}

【拆解規則】
1. 只針對 {asset_name} 這家公司本身，段落中其他公司的情境不要算進來。
2. stance：
   - "podcast"：節目主張會發生、支撐論點方向的情境
   - "risk"：節目自己提到、可能讓論點失敗的風險或反向情境
3. 每個情境都要附 quote，必須是從【驗證句】或【補充段落】逐字擷取的原文片段（20字以內），
   找不到原文依據就不要列這個情境——不要自行推測節目沒說的事。
4. chain：用 2~4 個短語寫出因果鏈，從節目說的事件開始，最後停在財務參數
   （營收、毛利率、費用率、本益比其中之一），例如 ["AI伺服器需求增加", "CoWoS產能滿載", "營收成長"]。
5. 同一個驅動因素只列一次，選最貼切的一個類型；同一句話如果同時講到漲價和需求，可以分開列兩個情境。
6. 如果論點完全沒有提到任何可以對應財務參數的具體驅動因素（例如只說「長期看好」「股價會漲」），
   scenarios 回傳空陣列，並在 no_driver_reason 用一句話說明。
7. 「回檔是買點」「股價超跌」「過度反應」這類只談股價位置、沒談基本面的說法，歸類為 RERATING_UP。

只能輸出合法 JSON，不可包含任何解釋文字或 markdown：
{{"scenarios": [{{"type": "<代碼>", "stance": "podcast"|"risk", "chain": ["...", "..."], "quote": "<原文片段>"}}],
 "no_driver_reason": "<scenarios為空時才填，否則填空字串>"}}
"""

PROMPT_V2 = """你是一位證券分析師，任務是把 Podcast 節目對「{asset_name}（{ticker}）」的投資論點，
拆解成可以對應到財務模型的「情境」，之後會用來試算 營收 → 毛利 → EPS → 股價。

【驗證句（節目對這檔股票的核心論點）】
方向：{direction}　時間：{timeframe}
論點：{thesis}
原文引述：{evidence_quote}

【補充段落（同一集節目裡，專門談 {asset_name} 的段落，以及有點名它的操作建議/風險提示）】
{context_text}

【情境類型清單（只能從這裡選，type 填代碼）】
{type_list}

【拆解規則】
1. 主體必須是 {asset_name} 本身：因果鏈的第一步，必須是發生在 {asset_name}、它的產品、
   它的客戶或它所在產業的事件。段落裡講其他公司自己的事（例如別家公司的新產品、別家公司的風險），不要列。
2. 只列「未來會發生的變化」。以下都不是情境，不要列：
   - 現況的優勢或特質（「毛利率高」「市佔率90%」「領先地位」「體質好」）
   - 已經公布的歷史數字（「上季營收年增62%」），這些只能當作其他情境的依據，不能自己成為一個情境
   - 「不受某風險影響」（沒有變化就不是情境）
3. 「需求」指的是公司產品/服務的需求，不是股票的買盤。股價走勢、均線、型態、K線、
   法人或散戶買賣超、融資增減、季節性、漲多拉回、牛市訊號，一律歸類為 TECHNICAL_FLOW。
4. RERATING_UP / RERATING_DOWN 只用在節目有給出估值依據時（本益比數字、跟歷史區間或同業比較、
   明說「低估」「估值過高」）。只說「回檔是買點」「股價超跌」而沒有估值依據的，歸類為 TECHNICAL_FLOW。
5. MACRO_EASING / MACRO_TIGHTENING 只在節目明確說這個總經因素會影響 {asset_name} 時才列；
   泛泛談大盤、利率、就業數據而沒有連結到 {asset_name} 的，不要列。
6. stance：
   - "podcast"：節目主張會發生、支撐論點方向的情境（看空論點的話，就是支撐看空的情境）
   - "risk"：節目自己提到、可能讓論點失敗的風險或反向情境
7. 每個情境都要附 quote：從【驗證句】或【補充段落】逐字複製一段連續原文（20字以內），
   不可以改寫、不可以用「...」或「…」把兩段拼起來。找不到原文依據就不要列。
8. chain：用 2~4 個短語寫出因果鏈，從節目說的事件開始，最後停在財務參數
   （營收、毛利率、費用率、本益比其中之一；TECHNICAL_FLOW 停在「股價」），
   例如 ["AI伺服器需求增加", "CoWoS產能滿載", "營收成長"]。
9. 同一個驅動因素只列一次：同一件事不要同時列成 DEMAND_UP 和 NEW_BUSINESS，選最貼切的一個；
   不同的驅動因素（例如漲價和出貨增加）才分開列。
10. 如果論點沒有任何基本面或技術面的具體驅動因素（例如只說「長期看好」「值得佈局」），
    scenarios 回傳空陣列，並在 no_driver_reason 用一句話說明。

只能輸出合法 JSON，不可包含任何解釋文字或 markdown：
{{"scenarios": [{{"type": "<代碼>", "stance": "podcast"|"risk", "chain": ["...", "..."], "quote": "<原文片段>"}}],
 "no_driver_reason": "<scenarios為空時才填，否則填空字串>"}}
"""

# v3：stance 不再讓 AI 判斷(改輸出 effect，由程式推 stance，見 derive_stance)，補空泛句反例。
PROMPT_V3 = """你是一位證券分析師，任務是把 Podcast 節目對「{asset_name}（{ticker}）」的投資論點，
拆解成可以對應到財務模型的「情境」，之後會用來試算 營收 → 毛利 → EPS → 股價。

【驗證句（節目對這檔股票的核心論點）】
方向：{direction}　時間：{timeframe}
論點：{thesis}
原文引述：{evidence_quote}

【補充段落（同一集節目裡，專門談 {asset_name} 的段落，以及有點名它的操作建議/風險提示）】
{context_text}

【情境類型清單（只能從這裡選，type 填代碼）】
{type_list}

【拆解規則】
1. 主體必須是 {asset_name} 本身：因果鏈的第一步，必須是發生在 {asset_name}、它的產品、
   它的客戶或它所在產業的事件。段落裡講其他公司自己的事（例如別家公司的新產品、別家公司的風險），不要列。
2. 只列「未來會發生的變化」。以下都不是情境，不要列：
   - 現況的優勢或特質（「毛利率高」「市佔率90%」「領先地位」「體質好」）
   - 已經公布的歷史數字（「上季營收年增62%」），這些只能當作其他情境的依據，不能自己成為一個情境
   - 「不受某風險影響」（沒有變化就不是情境）
   - 今天/近期已經發生的股價漲跌（「今日收盤漲30元」）
3. 「需求」指的是公司產品/服務的需求，不是股票的買盤。股價走勢、均線、型態、K線、
   法人或散戶買賣超、融資增減、季節性、漲多拉回、牛市訊號、「市場提前反應」，一律歸類為 TECHNICAL_FLOW。
4. RERATING_UP / RERATING_DOWN 只用在節目有給出估值依據時（本益比數字、跟歷史區間或同業比較、
   明說「低估」「估值過高」「合理價」）。只說「回檔是買點」「漲幅過大」「股價有上升空間」而沒有估值依據的，
   歸類為 TECHNICAL_FLOW。
5. MACRO_EASING / MACRO_TIGHTENING 只在節目明確說這個總經因素會影響 {asset_name} 時才列；
   泛泛談大盤、利率、就業數據而沒有連結到 {asset_name} 的，不要列。
6. effect：這個情境對 {asset_name} 是利多("up")還是利空("down")，只看情境本身，不用管論點方向。
7. 每個情境都要附 quote：從【驗證句】或【補充段落】逐字複製一段連續原文（20字以內），
   不可以改寫、不可以用「...」或「…」把兩段拼起來。找不到原文依據就不要列。
8. chain：用 2~4 個短語寫出因果鏈，從節目說的事件開始，最後停在財務參數
   （營收、毛利率、費用率、本益比其中之一；TECHNICAL_FLOW 停在「股價」），
   例如 ["AI伺服器需求增加", "CoWoS產能滿載", "營收成長"]。
9. 同一個驅動因素只列一次：同一件事不要同時列成 DEMAND_UP 和 NEW_BUSINESS，選最貼切的一個；
   不同的驅動因素（例如漲價和出貨增加）才分開列。
10. 空泛的說法不是情境，不要列，例如：
    - 「長期看好」「值得佈局」「持有不會後悔」「未來幾年表現亮眼」
    - 「馬斯克會把科技帶到新的高度」（沒有具體產品、客戶或事件）
    - 「僅需關注景氣」「要留意總經變化」（只提醒要注意，沒說會往哪個方向變）
    不可以從一個中性的字眼，自己推出「往上」和「往下」兩個相反的情境。
    如果整段論點都是這類空泛說法，scenarios 回傳空陣列，並在 no_driver_reason 用一句話說明。

只能輸出合法 JSON，不可包含任何解釋文字或 markdown：
{{"scenarios": [{{"type": "<代碼>", "effect": "up"|"down", "chain": ["...", "..."], "quote": "<原文片段>"}}],
 "no_driver_reason": "<scenarios為空時才填，否則填空字串>"}}
"""

VERSIONS = {
    "v1": {"prompt": PROMPT_V1, "types": SCENARIO_TYPES},
    "v2": {"prompt": PROMPT_V2, "types": SCENARIO_TYPES_V2},
    "v3": {"prompt": PROMPT_V3, "types": SCENARIO_TYPES_V2},
}


def build_type_list(types: dict) -> str:
    lines = []
    for code, t in types.items():
        levers = "、".join(f"{LEVER_ZH[k]}{'↑' if v > 0 else '↓'}" for k, v in t["levers"].items()) or "不對應財務參數"
        lines.append(f"- {code}：{t['name']}（影響：{levers}）")
    return "\n".join(lines)


def sample_records(n: int, seed: int, max_per_ticker: int = 0) -> list:
    """
    max_per_ticker=0 是 v1/v2 當初的抽法(照驗證句數量隨機抽，台積電/特斯拉/輝達會佔一大半)；
    >0 時每檔股票最多抽這麼多句，讓樣本涵蓋更多不同公司。
    """
    fin_tickers = set(FinancialReportCache.objects.using("summariesdb").values_list("ticker", flat=True).distinct())
    rows = list(
        BacktestingRecord.objects.using("summariesdb")
        .filter(summary__mode="pro", ticker__in=fin_tickers)
        .exclude(thesis="")
        .select_related("summary")
        .order_by("id")
    )
    # 同一句論點可能重複出現(同一集重跑)，依(ticker, thesis)去重
    seen, uniq = set(), []
    for r in rows:
        key = (r.ticker, r.thesis.strip())
        if key in seen or NON_STOCK_PATTERN.search(r.ticker):
            continue
        seen.add(key)
        uniq.append(r)

    # 看空的驗證句本來就少，分層抽樣確保有幾筆看空的，才看得出 risk/負向情境分得準不準
    rng = random.Random(seed)
    bearish = [r for r in uniq if r.direction != "bullish"]
    bullish = [r for r in uniq if r.direction == "bullish"]
    n_bear = min(len(bearish), max(1, n // 5))
    if not max_per_ticker:
        picked = rng.sample(bearish, n_bear) + rng.sample(bullish, n - n_bear)
    else:
        per_ticker = {}

        def take(pool, k):
            out = []
            for r in rng.sample(pool, len(pool)):
                if len(out) == k:
                    break
                if per_ticker.get(r.ticker, 0) < max_per_ticker:
                    per_ticker[r.ticker] = per_ticker.get(r.ticker, 0) + 1
                    out.append(r)
            return out

        picked = take(bearish, n_bear)
        picked += take(bullish, n - len(picked))
    print(f"候選 {len(uniq)} 筆(看多 {len(bullish)}、其他 {len(bearish)})，抽出 {len(picked)} 筆、"
          f"{len({r.ticker for r in picked})} 檔股票")
    return picked


def build_context(record, version: str) -> str:
    """
    v1：同一集裡這檔股票的「個股：X」段落 + 總經 + 有點名它的操作建議/風險提示
        (直接沿用 build_classification_context)。
    v2：拿掉「總體經濟環境」整段——那是整集共用的背景，v1 的總經情境幾乎都是從這裡滲進來的。
    找不到這檔股票的段落就只用驗證句。
    """
    try:
        topics = find_grounded_stock_topics(record.summary)
    except Exception:
        topics = []
    match = next((t for t in topics if t["ticker"] == record.ticker), None)
    if not match:
        return "(無)"
    asset_name, topic = match["asset_name"], match["topic"]
    if version == "v1":
        context_text, _ = build_classification_context(record.summary, asset_name, record.ticker, topic)
        return context_text

    # 跟 build_classification_context 同一套取段落規則，只是不放「總體經濟環境」
    parts = [f"【個股：{asset_name}】\n{topic.get('summary', '')}\n{topic.get('position', '')}"]
    for a in record.summary.arguments or []:
        if not isinstance(a, dict) or a.get("topic") not in ("操作策略與建議", "風險提示"):
            continue
        blob = json.dumps(a, ensure_ascii=False)
        if asset_name in blob or record.ticker in blob:
            parts.append(f"【{a.get('topic')}（有點名此股票）】\n{a.get('summary', '')}\n{a.get('position', '')}")
    return "\n\n".join(parts)


_PUNCT_RE = re.compile(r"[\s，,。．.、；;：:！!？?「」『』（）()【】\[\]\"'“”‘’…—\-–~～/]")


def normalize_text(s: str) -> str:
    """比對 quote 用：去掉全形/半形標點與空白，只留文字本身。"""
    return _PUNCT_RE.sub("", s or "")


def dedupe_scenarios(scenarios: list) -> tuple[list, int]:
    """同一個(type, stance)只留第一筆，其餘的 quote 併進去當額外依據。回傳(去重後, 被併掉幾筆)。"""
    merged, order = {}, []
    for s in scenarios:
        key = (s.get("type"), s.get("stance"))
        if key not in merged:
            merged[key] = {**s, "extra_quotes": []}
            order.append(key)
        else:
            merged[key]["extra_quotes"].append(s.get("quote", ""))
    return [merged[k] for k in order], len(scenarios) - len(order)


# ── v3 程式端檢查 ─────────────────────────────────────────────────────────
# v2 的 prompt 已經寫了「股價買盤不是需求」「沒估值依據不算 RERATING」，模型還是常常沒照做，
# 這兩條改由程式強制，不再只靠 prompt。
_VALUATION_RE = re.compile(r"本益比|P/?E|倍|低估|高估|估值|合理價|目標價|股價淨值比|殖利率")
_PRICE_END_RE = re.compile(r"股價|漲幅|跌幅|反彈|拉回")


def lever_effect(scenario_type: str) -> int:
    """
    情境類型對公司的淨效果(+1利多/-1利空/0看不出來)，由 SCENARIO_TYPES 的 levers 推：
    營業費用率是越低越好，所以方向要反過來算。CAPACITY_EXPANSION(營收↑毛利↓)、
    TECHNICAL_FLOW(沒有 levers) 會算出 0，交給 AI 的 effect 決定。
    """
    levers = SCENARIO_TYPES_V2.get(scenario_type, {}).get("levers", {})
    total = sum(-v if k == "opex_ratio" else v for k, v in levers.items())
    return (total > 0) - (total < 0)


def apply_checks(scenarios: list, thesis_direction: str) -> list:
    """
    依序做三件事，每筆情境記下被改了什麼(fixes)，給人工檢查用：
      1. 因果鏈終點是股價(不是營收/毛利/費用率/本益比)，就改成 TECHNICAL_FLOW
      2. RERATING_UP/DOWN 的 quote 沒有估值字眼，就降級成 TECHNICAL_FLOW
      3. stance 由程式推：情境效果跟論點方向一致 = support，相反 = against
         (效果優先用類型本身的 levers；看不出來才用 AI 給的 effect)
    """
    out = []
    for s in scenarios:
        s = {**s, "fixes": []}
        chain_end = (s.get("chain") or [""])[-1]
        if s.get("type") in ("RERATING_UP", "RERATING_DOWN"):
            # 估值類只看有沒有估值依據，不套用終點檢查——「EPS 15.88元×15倍本益比 → 目標價225元」
            # 這種最標準的估值論點，因果鏈終點本來就會寫到股價(第一輪實測就誤殺了鴻海這筆)
            if not _VALUATION_RE.search(s.get("quote", "")):
                s["fixes"].append(f"{s['type']}→TECHNICAL_FLOW(沒有估值依據)")
                s["type"] = "TECHNICAL_FLOW"
        elif s.get("type") != "TECHNICAL_FLOW" and _PRICE_END_RE.search(chain_end):
            s["fixes"].append(f"{s['type']}→TECHNICAL_FLOW(因果鏈終點是股價)")
            s["type"] = "TECHNICAL_FLOW"

        effect = lever_effect(s.get("type")) or {"up": 1, "down": -1}.get(s.get("effect"), 0)
        s["effect"] = {1: "up", -1: "down"}.get(effect, s.get("effect", ""))
        thesis_sign = {"bullish": 1, "bearish": -1}.get(thesis_direction, 0)
        if thesis_sign and effect:
            s["stance"] = "support" if effect == thesis_sign else "against"
        else:
            s["stance"] = s["effect"] or "unknown"  # 中性論點：只標利多/利空
        out.append(s)
    return out


def dedupe_scenarios_v3(scenarios: list) -> tuple[list, int]:
    """
    同一個(type, stance)只留一筆，但優先留「quote 對得到原文、沒被程式改過類型」的那筆——
    v2 只留第一筆，#13 台積電就留下了錯的「底部有買盤支撐」，把對的「美元營收年成長24%」併掉了。
    """
    groups, order = {}, []
    for s in scenarios:
        key = (s.get("type"), s.get("stance"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(s)

    result = []
    for key in order:
        items = sorted(groups[key], key=lambda s: (not s.get("quote_ok", True), bool(s.get("fixes"))))
        best = {**items[0], "extra_quotes": [s.get("quote", "") for s in items[1:]]}
        result.append(best)
    return result, len(scenarios) - len(result)


def validate(obj: dict, source_text: str, types: dict) -> list[str]:
    """程式端檢查：類型代碼是否在清單內、quote 是否真的出現在輸入文字裡(防止 AI 捏造依據)。"""
    issues = []
    normalized_source = normalize_text(source_text)
    for s in obj.get("scenarios", []):
        if s.get("type") not in types:
            issues.append(f"未知類型 {s.get('type')}")
        if s.get("stance") not in ("podcast", "risk", "support", "against", "up", "down"):
            issues.append(f"未知 stance {s.get('stance')}")
        quote = normalize_text(s.get("quote", ""))
        if quote and quote not in normalized_source:
            issues.append(f"quote 找不到原文：{s.get('quote')}")
    return issues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=list(VERSIONS), default="v2")
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default=os.getenv("THESIS_SCENARIO_MODEL", "gemini-2.5-flash"))
    parser.add_argument("--max-per-ticker", type=int, default=0,
                        help="每檔股票最多抽幾句，0=不限制(v1/v2 當初的抽法)")
    args = parser.parse_args()

    cfg = VERSIONS[args.version]
    records = sample_records(args.n, args.seed, args.max_per_ticker)
    client = make_client()
    type_list = build_type_list(cfg["types"])

    results = []
    for i, r in enumerate(records, 1):
        context_text = build_context(r, args.version)
        prompt = cfg["prompt"].format(
            asset_name=r.asset, ticker=r.ticker, direction=r.direction, timeframe=r.timeframe_raw or "(未標示)",
            thesis=r.thesis, evidence_quote=r.evidence_quote or "(無)", context_text=context_text, type_list=type_list,
        )
        t0 = time.time()
        try:
            resp = gemini_generate_with_retry(
                client=client, model=args.model, prompt_text=prompt,
                temperature=0.0, max_output_tokens=1500, max_tries=3,
            )
            obj = json.loads(sanitize_json_text(getattr(resp, "text", "") or ""))
            error = None
        except Exception as e:
            obj, error = {"scenarios": [], "no_driver_reason": ""}, str(e)[:200]

        source_text = f"{r.thesis}\n{r.evidence_quote}\n{context_text}"
        n_merged = 0
        if args.version == "v2":
            obj["scenarios"], n_merged = dedupe_scenarios(obj.get("scenarios", []))
        elif args.version == "v3":
            normalized_source = normalize_text(source_text)
            for s in obj.get("scenarios", []):
                s["quote_ok"] = normalize_text(s.get("quote", "")) in normalized_source
            # 留一份 AI 原始輸出，之後改檢查規則可以離線重算，不用再打一次 API
            obj["ai_scenarios"] = [dict(s) for s in obj.get("scenarios", [])]
            checked = apply_checks(obj.get("scenarios", []), r.direction)
            obj["raw_scenarios"] = checked  # 去重前的完整結果，才看得到被併掉的那幾筆有沒有被程式改過
            obj["scenarios"], n_merged = dedupe_scenarios_v3(checked)

        issues = validate(obj, source_text, cfg["types"]) if not error else []
        results.append({
            "id": r.id, "ticker": r.ticker, "asset": r.asset, "direction": r.direction,
            "timeframe": r.timeframe_raw, "thesis": r.thesis, "evidence_quote": r.evidence_quote,
            "has_context": context_text != "(無)", "result": obj, "n_merged": n_merged,
            "issues": issues, "error": error,
        })
        print(f"[{i}/{len(records)}] {r.ticker} {len(obj.get('scenarios', []))} 個情境"
              f"{f'(併掉{n_merged})' if n_merged else ''} ({time.time() - t0:.1f}s)"
              f"{' ⚠ ' + '; '.join(issues) if issues else ''}{' ✗ ' + error if error else ''}")

    os.makedirs("out", exist_ok=True)
    suffix = "" if args.version == "v1" else f"_{args.version}"
    if args.max_per_ticker:
        suffix += f"_cap{args.max_per_ticker}"
    path = f"out/thesis_scenario_sample{suffix}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for row in results:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"已輸出 {path}")


if __name__ == "__main__":
    main()
