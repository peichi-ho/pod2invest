# apps/summaries/services/thesis_scenario.py
"""
論點情境萃取：把驗證句(BacktestingRecord)拆成可以對應到財務模型的「情境」，
給「論點情境試算」用(需求↑ → 營收↑ → 毛利率↑ → EPS↑ → 股價↑)。

設計原則：AI 只做「定性」——從固定的 SCENARIO_TYPES 挑出這句論點包含哪些情境、
每個情境對公司是利多還是利空、因果鏈怎麼走、原文依據是哪一句。
  - 每個情境「影響哪個財務參數、往哪個方向」由 SCENARIO_TYPES 的 levers 決定，不讓 AI 決定。
  - 「影響幅度」之後由該公司歷史財報分布給預設值，這裡完全不碰數字。
  - 「支持/反對論點」(stance) 由程式依 levers 跟論點方向推，不讓 AI 判斷。

prompt 跟檢查規則是 scripts/prototype_thesis_scenario.py 跑過三輪(v1→v3)、每輪抽30句人工
檢查後的版本：v3 在每檔最多抽2句的樣本(30句/23檔)上，人工判斷有問題的情境約7%。
這裡比原型多一條檢查：總經情境的原文/因果鏈必須提到這家公司(原型 v3 還會混進
「歐盟對蘋果開罰」「CPI 高於預期」這種跟公司無關的總經新聞)。

v4(2026-10-20)：v3 正式跑前50筆時發現情境清單缺類別——「法人預估EPS」「外資目標價」
「負債/融資/併購成本」被硬塞進 DEMAND_UP/RERATING_UP/COST_UP，銀行/壽險根本不適用
營收→毛利這條鏈。新增 EPS_FORECAST/ANALYST_TARGET/FINANCIAL_RISK、排除金融股、
quote 改模糊比對、MIX_UPGRADE/NEW_BUSINESS 擋掉「創新高、已達、龍頭」這類現況。

v5(2026-10-20)：v4 的數字只存成一串沒有標籤的 values，分不出是 EPS、本益比還是股價
(台積電的 EPS 預估裡混進了 2000 元股價)，合併兩句話時數字也被混在一起。改成依類型
存有標籤的 figures(EPS_FORECAST:eps+year、RERATING:pe、ANALYST_TARGET:target_low/high)，
不同數字的情境不合併。另外「倍」要數字加倍才算估值依據(「翻倍」不算)、現況檢查也套用到 PRICE_UP。

已知限制：temperature=0 也不完全穩定(原型同一批30句跑兩次有4句結果不同)，所以結果一律
批次算好存進 ThesisScenarioExtraction，不在使用者打開頁面時即時呼叫。
"""
import json
import re
from typing import Optional

from .gemini import gemini_generate_with_retry, sanitize_json_text
from .sentiment_score import find_grounded_stock_topics

PROMPT_VERSION = "v5"

# levers：這個情境會動到哪些財務參數、方向(+1/-1)。
#   revenue_growth 營收成長率 / gross_margin 毛利率 / opex_ratio 營業費用率 /
#   non_operating 業外損益(利息、投資損益、一次性費用) / pe 本益比
# 空的 levers(TECHNICAL_FLOW)代表「節目有講、但不是基本面」，試算時只顯示不計算。
# sets / reference：情境本身帶著原文數字(figures)，試算時直接拿來用——
#   EPS_FORECAST 的數字當「直接指定 EPS」的預設值，ANALYST_TARGET 的數字畫成參考線。
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
    "RERATING_UP":        {"name": "估值偏低、本益比低於歷史區間（要有估值依據）", "levers": {"pe": +1}},
    "RERATING_DOWN":      {"name": "估值偏高、本益比高於歷史區間（要有估值依據）", "levers": {"pe": -1}},
    "MACRO_EASING":       {"name": "降息、資金寬鬆",             "levers": {"pe": +1}},
    "MACRO_TIGHTENING":   {"name": "升息、資金緊縮、總經風險",   "levers": {"pe": -1}},
    "TECHNICAL_FLOW":     {"name": "技術面、籌碼面、股價走勢（均線、型態、法人/散戶買賣超、季節性、漲多拉回）", "levers": {}},
    # v4 新增(v3 跑前50筆時，這三類被硬塞進 DEMAND_UP/RERATING_UP/COST_UP)
    "EPS_FORECAST":       {"name": "對未來 EPS/獲利的預估或上修下修（法人、公司或節目給的）", "levers": {}, "sets": "eps"},
    "ANALYST_TARGET":     {"name": "法人、外資、券商給的目標價（沒有本益比計算過程）", "levers": {}, "reference": "target_price"},
    "FINANCIAL_RISK":     {"name": "財務風險（負債、融資成本、信用違約、投資或匯兌損失、併購成本、訴訟賠償、會計疑慮）",
                           "levers": {"non_operating": -1}},
}

LEVER_ZH = {"revenue_growth": "營收成長", "gross_margin": "毛利率", "opex_ratio": "費用率",
            "non_operating": "業外損益", "pe": "P/E"}

# 銀行、壽險、券商的獲利來自利差/投資收益，沒有「營收 → 毛利 → EPS」這條鏈，不拆情境。
# 健康保險(UNH)、信用卡(Visa)、交易所有一般的營收/營業利益結構，不排除。
FINANCIAL_INDUSTRY_PATTERN = re.compile(r"^金融保險業$|銀行|^保險|資本市場")

# 期貨(GC=F)、指數(^TWII)、加密貨幣(BTC-USD)沒有財報，不拆情境
NON_STOCK_PATTERN = re.compile(r"=F$|^\^|-USD$")

PROMPT_TEMPLATE = """你是一位證券分析師，任務是把 Podcast 節目對「{asset_name}（{ticker}）」的投資論點，
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
   - 特別注意 MIX_UPGRADE 和 NEW_BUSINESS：「毛利率創十年新高」「市佔率已達六成」「龍頭/壟斷/領頭羊地位」
     都是已經發生的現況，不是情境。MIX_UPGRADE 只用在節目說「接下來」高毛利產品的佔比會提高
     （例如新產品放量、高階機種比重上升）。
3. 「需求」指的是公司產品/服務的需求，不是股票的買盤。股價走勢、均線、型態、K線、
   法人或散戶買賣超、融資增減、季節性、漲多拉回、牛市訊號、「市場提前反應」，一律歸類為 TECHNICAL_FLOW。
4. RERATING_UP / RERATING_DOWN 只用在節目有給出估值依據時（本益比數字、跟歷史區間或同業比較、
   明說「低估」「估值過高」）。只說「回檔是買點」「漲幅過大」「股價有上升空間」而沒有估值依據的，
   歸類為 TECHNICAL_FLOW。
4-1. 估值、獲利預估、目標價這三類的分法：
   - 有本益比倍數的估值（「EPS 75元 × 20倍本益比」「本益比低於15倍」）→ RERATING_UP / RERATING_DOWN
   - 對未來 EPS 或獲利的預估、上修、下修（「明年EPS上看100元」「EPS預估持續上調」）→ EPS_FORECAST。
     同一句同時有 EPS 數字和本益比倍數時，兩個都列。
   - 只有目標價、沒有本益比計算過程（「高盛目標價400元」「外資目標價上看2000元」）→ ANALYST_TARGET
4-2. 負債、融資成本、信用違約交換(CDS)、投資或匯兌損失、併購的分手費/整合成本、訴訟賠償、會計疑慮 → FINANCIAL_RISK，
   不要歸到 COST_UP（COST_UP 只用在原物料、製造成本、折舊這類會影響毛利率的成本）。
5. MACRO_EASING / MACRO_TIGHTENING 只在節目明確說這個總經因素會影響 {asset_name} 時才列；
   泛泛談大盤、利率、就業數據而沒有連結到 {asset_name} 的，不要列。
6. effect：這個情境對 {asset_name} 是利多("up")還是利空("down")，只看情境本身，不用管論點方向。
7. 每個情境都要附 quote：從【驗證句】或【補充段落】逐字複製一段連續原文（20字以內），
   不可以改寫、不可以用「...」或「…」把兩段拼起來。找不到原文依據就不要列。
8. chain：用 2~4 個短語寫出因果鏈，從節目說的事件開始，最後停在財務參數
   （營收、毛利率、費用率、業外損益、本益比、EPS、目標價其中之一；TECHNICAL_FLOW 停在「股價」），
   例如 ["AI伺服器需求增加", "CoWoS產能滿載", "營收成長"]。
8-1. figures：只有下面三類要填，其他類型一律填 {{}}。數字只能從 quote 照抄，quote 裡沒有就填 null，
   不可以自己計算、換算或推估：
   - EPS_FORECAST：{{"eps": 每股盈餘數字, "year": 四位數年份}}。year 只在 quote 寫出年份數字時填（「2026年」→ 2026），
     寫「今年」「明年」或沒寫就填 null。營收成長率、營收金額不是 EPS——那種情況不要用 EPS_FORECAST，
     改用 DEMAND_UP / DEMAND_DOWN。
   - RERATING_UP / RERATING_DOWN：{{"pe": 本益比倍數}}，例如「以20倍本益比計算」→ {{"pe": 20}}。
   - ANALYST_TARGET：{{"target_low": 目標價, "target_high": 目標價}}。單一目標價兩個都填同一個數字；
     區間（「1880至2330」）填下緣和上緣。
   同一句話有兩個不同年份的 EPS（「今年85元、明年100元」），列成兩個 EPS_FORECAST。
9. 同一個驅動因素只列一次：同一件事不要同時列成 DEMAND_UP 和 NEW_BUSINESS，選最貼切的一個；
   不同的驅動因素（例如漲價和出貨增加）才分開列。
10. 空泛的說法不是情境，不要列，例如：
    - 「長期看好」「值得佈局」「持有不會後悔」「未來幾年表現亮眼」
    - 「馬斯克會把科技帶到新的高度」（沒有具體產品、客戶或事件）
    - 「僅需關注景氣」「要留意總經變化」（只提醒要注意，沒說會往哪個方向變）
    不可以從一個中性的字眼，自己推出「往上」和「往下」兩個相反的情境。
    如果整段論點都是這類空泛說法，scenarios 回傳空陣列，並在 no_driver_reason 用一句話說明。

只能輸出合法 JSON，不可包含任何解釋文字或 markdown：
{{"scenarios": [{{"type": "<代碼>", "effect": "up"|"down", "chain": ["...", "..."], "quote": "<原文片段>", "figures": {{}}}}],
 "no_driver_reason": "<scenarios為空時才填，否則填空字串>"}}
"""


def build_type_list() -> str:
    lines = []
    for code, t in SCENARIO_TYPES.items():
        levers = "、".join(f"{LEVER_ZH[k]}{'↑' if v > 0 else '↓'}" for k, v in t["levers"].items()) or "不對應財務參數"
        lines.append(f"- {code}：{t['name']}（影響：{levers}）")
    return "\n".join(lines)


def build_context(record) -> str:
    """
    同一集裡這檔股票的「個股：X」段落 + 有點名它的操作建議/風險提示。
    刻意不放「總體經濟環境」整段——那是整集共用的背景，原型 v1 的總經情境幾乎都是從那裡
    滲進來、跟這家公司無關。找不到這檔股票的段落就回傳「(無)」，只用驗證句本身。
    """
    try:
        topics = find_grounded_stock_topics(record.summary)
    except Exception:
        topics = []
    match = next((t for t in topics if t["ticker"] == record.ticker), None)
    if not match:
        return "(無)"
    asset_name, topic = match["asset_name"], match["topic"]
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


# ── 程式端檢查 ────────────────────────────────────────────────────────────
# 原型 v2 的 prompt 已經寫了「股價買盤不是需求」「沒估值依據不算 RERATING」，模型還是常常
# 沒照做，這些改由程式強制。
# v4：「目標價」從估值字眼拿掉——只有目標價、沒有本益比的改歸 ANALYST_TARGET。
# 「倍」要有數字(「20倍」)，v4 讓「股價有翻倍潛力」也通過了估值檢查
_VALUATION_RE = re.compile(r"本益比|P/?E|\d+(?:\.\d+)?\s*倍|低估|高估|估值|合理價|股價淨值比|殖利率")
_PRICE_END_RE = re.compile(r"股價|漲幅|跌幅|反彈|拉回")
# 已經發生的現況(v3 前50筆：「毛利創十年新高」「市佔已達六成」「領頭羊地位」被當成會改善)
# v5 起也套用到 PRICE_UP(v4 把 Seagate「毛利也是近十年新高」當成漲價)
_CURRENT_STATE_TYPES = {"MIX_UPGRADE", "NEW_BUSINESS", "PRICE_UP"}
_CURRENT_STATE_RE = re.compile(r"創.{0,6}新高|近.{0,4}年.{0,2}新高|已達|已經達|領導地位|龍頭|壟斷|領頭羊|獨門")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
MIN_QUOTE_CHARS = 4      # 「看多」這種兩個字的 quote 沒有依據價值
FUZZY_MATCH_RATIO = 0.8  # quote 每一段至少要有 8 成連續對得上原文

# 這些類型的因果鏈終點本來就會寫到股價/目標價，不套用「終點是股價就改技術面」的檢查
_PRICE_END_EXEMPT = {"RERATING_UP", "RERATING_DOWN", "EPS_FORECAST", "ANALYST_TARGET"}


def lever_effect(scenario_type: str) -> int:
    """
    情境類型對公司的淨效果(+1利多/-1利空/0看不出來)，由 levers 推：營業費用率越低越好，
    方向反過來算。CAPACITY_EXPANSION(營收↑毛利↓)、TECHNICAL_FLOW/EPS_FORECAST/ANALYST_TARGET
    (沒有 levers) 會算出 0，交給 AI 給的 effect 決定。
    """
    levers = SCENARIO_TYPES.get(scenario_type, {}).get("levers", {})
    total = sum(-v if k == "opex_ratio" else v for k, v in levers.items())
    return (total > 0) - (total < 0)


def _company_aliases(asset_name: str, ticker: str) -> list[str]:
    """總經情境要提到這家公司才算數，用名稱跟代號主體(2330.TW → 2330)比對。"""
    return [a for a in {asset_name.strip(), ticker.split(".")[0].strip()} if a]


def match_quote(quote: str, normalized_source: str) -> str:
    """
    回傳 "exact" / "fuzzy" / ""(對不到)。
    v3 要求逐字相同，前50筆裡有3筆正確的情境只因為字句差一點就被丟掉(例如原文是
    「AI的長期成長性還是很看好」、quote 少了幾個字)。改成：quote 先依省略號切段，
    每一段都要有 FUZZY_MATCH_RATIO 以上的連續文字出現在原文裡。
    """
    from difflib import SequenceMatcher

    q = normalize_text(quote)
    if not q:
        return ""
    if q in normalized_source:
        return "exact"
    parts = [normalize_text(p) for p in re.split(r"\.{2,}|…+", quote)]
    parts = [p for p in parts if len(p) >= 2]
    if not parts:
        return ""
    for part in parts:
        m = SequenceMatcher(None, normalized_source, part, autojunk=False).find_longest_match(
            0, len(normalized_source), 0, len(part))
        if m.size < len(part) * FUZZY_MATCH_RATIO:
            return ""
    return "fuzzy"


# 每種類型可以帶哪些數字欄位
FIGURE_FIELDS = {
    "EPS_FORECAST":   ("eps", "year"),
    "RERATING_UP":    ("pe",),
    "RERATING_DOWN":  ("pe",),
    "ANALYST_TARGET": ("target_low", "target_high"),
}


def _to_number(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def verify_figures(scenario_type: str, raw_figures, quote: str, published_year: Optional[int]) -> tuple[dict, list]:
    """
    只留這個類型該有的欄位、而且數字真的出現在 quote 裡(去掉千分位逗號比對)，
    不讓 AI 自己算出新數字。回傳(figures, 被移除的說明)。

    year 例外：quote 寫「今年」「明年」時 AI 依規則填 null，這裡用節目發布年份換算，
    並標 year_inferred=True——發布日是程式知道的事實，不是 AI 推估的。
    """
    fields = FIGURE_FIELDS.get(scenario_type)
    if not fields:
        return {}, []
    raw = raw_figures if isinstance(raw_figures, dict) else {}
    in_quote = {float(n.replace(",", "")) for n in _NUMBER_RE.findall(quote)}

    figures, removed = {}, []
    for f in fields:
        v = _to_number(raw.get(f))
        if v is None:
            figures[f] = None
        elif v in in_quote:
            figures[f] = int(v) if f == "year" else v
        else:
            figures[f] = None
            removed.append(f"{f}={raw.get(f)} 對不到 quote，已移除")

    if scenario_type == "EPS_FORECAST" and figures.get("year") is None and published_year:
        if "明年" in quote:
            figures["year"], figures["year_inferred"] = published_year + 1, True
        elif "今年" in quote:
            figures["year"], figures["year_inferred"] = published_year, True
    if scenario_type == "ANALYST_TARGET" and figures.get("target_low") and figures.get("target_high") \
            and figures["target_low"] > figures["target_high"]:
        figures["target_low"], figures["target_high"] = figures["target_high"], figures["target_low"]
    return figures, removed


def apply_checks(scenarios: list, thesis_direction: str, source_text: str,
                 asset_name: str, ticker: str, published_year: Optional[int] = None) -> tuple[list, list]:
    """
    逐筆檢查 AI 輸出，回傳(保留的, 丟掉的)。每筆保留的情境會記下被改了什麼(fixes)：
      1. 類型代碼不在清單內、quote 太短或對不到原文(允許模糊比對) → 丟掉
      2. 總經情境的 quote/因果鏈沒提到這家公司 → 丟掉
      3. MIX_UPGRADE/NEW_BUSINESS/PRICE_UP 的 quote 是「創新高、已達、龍頭」這類現況 → 丟掉
      4. RERATING 的 quote 沒有估值字眼：有「目標價」改成 ANALYST_TARGET，否則降級成 TECHNICAL_FLOW
      5. 其他類型(估值、EPS預估、目標價除外)的因果鏈終點是股價 → 改成 TECHNICAL_FLOW
      6. figures 只留這個類型該有的欄位、而且 quote 裡真的有的數字(見 verify_figures)
      7. stance 由程式推：情境效果跟論點方向一致 = support，相反 = against；
         論點不是看多/看空時只標 up/down
    """
    normalized_source = normalize_text(source_text)
    aliases = _company_aliases(asset_name, ticker)
    thesis_sign = {"bullish": 1, "bearish": -1}.get(thesis_direction, 0)

    kept, dropped = [], []
    for raw in scenarios:
        s = {
            "type": raw.get("type"),
            "effect": raw.get("effect"),
            "chain": [str(c) for c in (raw.get("chain") or [])],
            "quote": str(raw.get("quote") or ""),
            "fixes": [],
        }
        if s["type"] not in SCENARIO_TYPES:
            dropped.append({**s, "reason": "未知類型"})
            continue
        if len(normalize_text(s["quote"])) < MIN_QUOTE_CHARS:
            dropped.append({**s, "reason": "quote 太短"})
            continue
        s["quote_match"] = match_quote(s["quote"], normalized_source)
        if not s["quote_match"]:
            dropped.append({**s, "reason": "quote 對不到原文"})
            continue
        if s["type"] in ("MACRO_EASING", "MACRO_TIGHTENING"):
            blob = s["quote"] + "".join(s["chain"])
            if not any(a in blob for a in aliases):
                dropped.append({**s, "reason": "總經情境沒有提到這家公司"})
                continue
        if s["type"] in _CURRENT_STATE_TYPES and _CURRENT_STATE_RE.search(s["quote"]):
            dropped.append({**s, "reason": "現況不是未來的變化"})
            continue

        chain_end = s["chain"][-1] if s["chain"] else ""
        if s["type"] in ("RERATING_UP", "RERATING_DOWN"):
            if not _VALUATION_RE.search(s["quote"]):
                new_type = "ANALYST_TARGET" if "目標價" in s["quote"] else "TECHNICAL_FLOW"
                reason = "只有目標價" if new_type == "ANALYST_TARGET" else "沒有估值依據"
                s["fixes"].append(f"{s['type']}→{new_type}({reason})")
                s["type"] = new_type
        elif s["type"] not in _PRICE_END_EXEMPT and s["type"] != "TECHNICAL_FLOW" \
                and _PRICE_END_RE.search(chain_end):
            s["fixes"].append(f"{s['type']}→TECHNICAL_FLOW(因果鏈終點是股價)")
            s["type"] = "TECHNICAL_FLOW"

        s["figures"], removed = verify_figures(s["type"], raw.get("figures"), s["quote"], published_year)
        s["fixes"] += removed

        effect = lever_effect(s["type"]) or {"up": 1, "down": -1}.get(s["effect"], 0)
        s["effect"] = {1: "up", -1: "down"}.get(effect, "")
        if thesis_sign and effect:
            s["stance"] = "support" if effect == thesis_sign else "against"
        else:
            s["stance"] = s["effect"] or "unknown"
        kept.append(s)
    return kept, dropped


def dedupe_scenarios(scenarios: list) -> list:
    """
    同一個(type, stance)只留一筆，優先留沒被程式改過類型的那筆，其他筆的 quote 併進
    extra_quotes 當額外依據(原型 v2 只留第一筆，台積電就留下了錯的「底部有買盤支撐」)。
    帶數字的情境(figures 有值)連數字一起當 key：「今年EPS 85元」「明年EPS 100元」是兩個
    不同的情境，不合併——v4 把它們的數字併成一串，就分不出哪個數字是哪一年的。
    """
    groups, order = {}, []
    for s in scenarios:
        figure_key = json.dumps(s.get("figures") or {}, sort_keys=True) \
            if any(v is not None for v in (s.get("figures") or {}).values()) else ""
        key = (s["type"], s["stance"], figure_key)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(s)

    result = []
    for key in order:
        items = sorted(groups[key], key=lambda s: (bool(s["fixes"]), s.get("quote_match") != "exact"))
        result.append({**items[0], "extra_quotes": [s["quote"] for s in items[1:]]})
    return result


def extract_thesis_scenarios(client, model: str, record) -> dict:
    """
    對一筆 BacktestingRecord 呼叫 Gemini 拆情境，回傳可以直接存進 ThesisScenarioExtraction 的 dict：
      status：ok(有情境) / no_driver(論點太空泛) / failed(呼叫或解析失敗)
      scenarios：檢查、去重後的情境；ai_scenarios：AI 原始輸出(之後改規則可以離線重算)；
      dropped：被檢查丟掉的情境跟原因。
    """
    context_text = build_context(record)
    prompt = PROMPT_TEMPLATE.format(
        asset_name=record.asset, ticker=record.ticker, direction=record.direction,
        timeframe=record.timeframe_raw or "(未標示)", thesis=record.thesis,
        evidence_quote=record.evidence_quote or "(無)", context_text=context_text,
        type_list=build_type_list(),
    )
    try:
        resp = gemini_generate_with_retry(
            client=client, model=model, prompt_text=prompt,
            temperature=0.0, max_output_tokens=4000, max_tries=3,
        )
        obj = json.loads(sanitize_json_text(getattr(resp, "text", "") or ""))
        ai_scenarios = obj.get("scenarios") or []
        if not isinstance(ai_scenarios, list):
            raise ValueError("scenarios 不是陣列")
    except Exception as e:
        return {"status": "failed", "scenarios": [], "ai_scenarios": [], "dropped": [],
                "no_driver_reason": "", "error": str(e)[:500]}

    source_text = f"{record.thesis}\n{record.evidence_quote}\n{context_text}"
    kept, dropped = apply_checks(
        [s for s in ai_scenarios if isinstance(s, dict)], record.direction, source_text,
        record.asset, record.ticker,
        published_year=record.summary.published_at.year if record.summary.published_at else None,
    )
    scenarios = dedupe_scenarios(kept)
    return {
        "status": "ok" if scenarios else "no_driver",
        "scenarios": scenarios,
        "ai_scenarios": ai_scenarios,
        "dropped": dropped,
        "no_driver_reason": str(obj.get("no_driver_reason") or "")[:500],
        "error": "",
    }


def is_extractable_ticker(ticker: Optional[str]) -> bool:
    return bool(ticker) and not NON_STOCK_PATTERN.search(ticker)


def financial_sector_tickers() -> set[str]:
    """
    IndustryClassification 裡屬於銀行/壽險/券商的 ticker(見 FINANCIAL_INDUSTRY_PATTERN)。
    分類表查不到的股票不會被排除——寧可多拆幾筆，也不要漏掉一般公司。
    """
    from apps.summaries.models import IndustryClassification

    rows = IndustryClassification.objects.using("summariesdb").values_list("ticker", "industry_name")
    return {t for t, name in rows if name and FINANCIAL_INDUSTRY_PATTERN.search(name)}
