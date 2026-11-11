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

# 正式使用的版本：計算機讀這個版本、新節目即時補拆也存成這個版本。
# v6(2026-10-27 起)是兩步做法：Flash 用 v6 prompt 拆 → 程式檢查 → REVIEW_MODEL 逐一判斷留或刪(extract_two_step)。
# v5 的資料保留在資料庫裡，要比較或退回時改這裡就好。
PROMPT_VERSION = "v6"
REVIEW_MODEL = "gemini-3.1-pro-preview"
REVIEW_VERSION = "r4"
REVIEW_LOCATION = "global"      # gemini-3.1-pro-preview 只在 Vertex 的 global 區域

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
    # 2026-10-26 從 FINANCIAL_RISK 拆出：反壟斷、訴訟、監管調查這類風險影響的是估值(不確定性)，
    # 不是 EPS。不在 prompt 讓 AI 直接選，由 apply_checks 依原文關鍵字從 FINANCIAL_RISK 改過來。
    "REGULATORY_RISK":    {"name": "法規風險（反壟斷、訴訟、監管調查、制裁）", "levers": {"pe": -1}, "check_only": True},
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


# v6(2026-10-27)：v5 抽樣 94 句裡 ✗/△ 的情境，
# 改成在 prompt 裡處理，並把原本靠關鍵字猜的 margin_drag、cost_kind 改由 AI 判斷：
#   泛論、否定句不列；情境要跟這句論點有關；「估值合理」不是低估；公司本身的問題不是總經；
#   新增 duration(短期/持續)、margin_drag、cost_kind、unmapped(清單沒有的驅動因素，估計漏拆用)
_V6_RULES = """11. 泛論和否定句不是情境，不要列：
    - 泛論：講一整類股票或一般規律，不是 {asset_name} 會發生的事（「虧轉盈的股票通常會大漲」「降息時成長股通常表現好」）
    - 否定或沒有變化：「不太會受到影響」「難以被超越」「有望抵銷關稅影響」「利潤率不太可能到三成」「AI營收貢獻不大」
      這類只說某件事不會發生、影響不大，沒有說會往哪個方向變的，不要列。
12. 情境必須是支持這句論點的理由，或是節目針對這句論點提到的風險。補充段落只用來找依據；
    段落裡跟這句論點無關的其他話題，不要列。
13. 估值：「估值合理」「估值不貴」「本益比相對安全」只表示沒有高估，不是低估，不要列成 RERATING_UP；
    要明說「低估」「便宜」「低於歷史區間/同業」才算。
14. 總經（MACRO_*）只用在利率、資金、景氣這類大環境因素。公司本身的問題（股價波動大、獲利能力受質疑、
    經營團隊、高管離職）不是總經，不要用 MACRO_*。總經是透過需求影響公司的（降息刺激汽車銷售），
    用 DEMAND_UP / DEMAND_DOWN；透過匯率、關稅影響成本的，用 COST_UP。
15. 擴產和新產品的分法：
    - 新產品開始量產、出貨（「機器人今年底生產1000台」）是 NEW_BUSINESS，不是 CAPACITY_EXPANSION
    - 公司為了自己使用而增加資本支出（不是為了賣更多產品或服務），不算擴產，不要列
    - 政府或客戶給的價格（支付率、費率、報價）調降是 PRICE_DOWN
16. 每個情境都要填 duration：
    - "short"：單月、單季、一次性或季節性的變化（「12月營收將月減9-12%」「本季有一次性費用」）
    - "lasting"：會持續一年以上，或看不出來只是短期的變化
17. CAPACITY_EXPANSION 要填 margin_drag（其他類型不用填）：
    - true：要自己投入大量資本支出——蓋新廠、海外設廠、擴建數據中心，折舊會增加、毛利率會被稀釋
    - false：利用現有或別人的閒置產能、提高稼動率、不需要大幅增加資本支出
18. COST_EFFICIENCY 要填 cost_kind（其他類型不用填）：
    - "cogs"：生產成本——良率、原料、製程、生產規模，影響毛利率
    - "opex"：營業費用——裁員、精簡組織、行銷或研發費用，影響營業費用率
    - "both"：兩種都有，或看不出來
19. unmapped：原文有提到具體的驅動因素，但清單裡找不到合適的類別時，列在 unmapped
    （driver 用一句話描述、quote 照抄原文），不要硬塞進某個類別；沒有就回傳空陣列。

只能輸出合法 JSON，不可包含任何解釋文字或 markdown：
{{"scenarios": [{{"type": "<代碼>", "effect": "up"|"down", "chain": ["...", "..."], "quote": "<原文片段>", "figures": {{}},
                 "duration": "short"|"lasting", "margin_drag": true|false, "cost_kind": "cogs"|"opex"|"both"}}],
 "unmapped": [{{"driver": "...", "quote": "<原文片段>"}}],
 "no_driver_reason": "<scenarios為空時才填，否則填空字串>"}}
"""
_V5_OUTPUT = PROMPT_TEMPLATE[PROMPT_TEMPLATE.index("只能輸出合法 JSON"):]
PROMPT_TEMPLATE_V6 = PROMPT_TEMPLATE.replace(_V5_OUTPUT, _V6_RULES)

# v7(試過把規則寫在類型旁、加自我檢查)在 41 句上沒有比 v6 好，已刪除，內容見 git 歷史。
PROMPTS = {"v6": PROMPT_TEMPLATE_V6}


def build_type_list() -> str:
    lines = []
    for code, t in SCENARIO_TYPES.items():
        if t.get("check_only"):
            continue
        levers = "、".join(f"{LEVER_ZH[k]}{'↑' if v > 0 else '↓'}" for k, v in t["levers"].items()) or "不對應財務參數"
        lines.append(f"- {code}：{t['name']}（影響：{levers}）")
    return "\n".join(lines)


def build_context(record, topics: Optional[list] = None) -> str:
    """
    同一集裡這檔股票的「個股：X」段落 + 有點名它的操作建議/風險提示。
    刻意不放「總體經濟環境」整段——那是整集共用的背景，原型 v1 的總經情境幾乎都是從那裡
    滲進來、跟這家公司無關。找不到這檔股票的段落就回傳「(無)」，只用驗證句本身。
    """
    if topics is None:
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
_VALUATION_RE = re.compile(r"本益比|本益成長比|PEG|P/?E|\d+(?:\.\d+)?\s*倍|低估|高估|估值|合理價|股價淨值比|殖利率")
_PRICE_END_RE = re.compile(r"股價|漲幅|跌幅|反彈|拉回")
# 已經發生的現況(v3 前50筆：「毛利創十年新高」「市佔已達六成」「領頭羊地位」被當成會改善)
# v5 起也套用到 PRICE_UP(v4 把 Seagate「毛利也是近十年新高」當成漲價)
# 2026-10-26 抽樣檢查(19類各5句)後擴大：「毛利率提升至接近50%」「具備規模與成本優勢」也是現況；
# 同時套用到成本下降。原文有未來字眼(將、預計、有望…)時不算現況，避免誤殺「毛利率將提升至55%」。
_CURRENT_STATE_TYPES = {"MIX_UPGRADE", "NEW_BUSINESS", "PRICE_UP", "COST_EFFICIENCY"}
_CURRENT_STATE_RE = re.compile(
    r"創.{0,6}新高|近.{0,4}年.{0,2}新高|已達|已經達|領導地位|龍頭|壟斷|領頭羊|獨門"
    r"|提升至|提高至|達到|具備.{0,8}優勢|具有.{0,8}優勢|擁有.{0,8}優勢")
_FUTURE_RE = re.compile(r"將|預計|預估|預期|有望|可望|可能|會|未來|明年|後年|下半年|逐步|持續")
# 目標價來源：原文提到法人、券商、分析師才算「法人」，否則是節目自己的看法
# 券商名稱要寫完整(元大證/元大投顧)：「300元大關」裡也有「元大」兩個字
_ANALYST_RE = re.compile(r"法人|外資|投信|券商|分析師|研究員|大摩|小摩|摩根|高盛|瑞銀|花旗|美銀|野村|凱基|元大(?:證|投|期|金)|富邦(?:證|投)|國泰證|群益|永豐證|麥格理|巴克萊|伯恩斯坦|Morgan|Goldman|UBS|Citi")
# 成本下降分兩種：生產成本(良率、原料、製程)只影響毛利率；組織成本(裁員、精簡、費用)只影響營業費用率
_COGS_RE = re.compile(r"良率|原料|材料|製程|生產|製造|採購|單位成本|產能利用")
_OPEX_RE = re.compile(r"裁員|人力|精簡|組織|費用|管銷|行銷|研發支出|營運成本|人事")
# 擴產只有在原文提到海外廠、新廠、折舊、稀釋時才讓毛利率下降(聯電借用 Intel 閒置產能就不會)
_MARGIN_DRAG_RE = re.compile(r"海外|美國廠|日本廠|德國廠|新廠|折舊|稀釋|擴廠|建廠")
# 總經情境依影響管道改分類：講需求的是營收面，講匯率/關稅/成本的是毛利面，只有利率/資金/估值才留在總經
_MACRO_DEMAND_RE = re.compile(r"需求|銷售|銷量|訂單|出貨|買氣|消費")
_MACRO_COST_RE = re.compile(r"匯|台幣|新台幣|美元|升值|貶值|關稅|成本")
# 財務風險裡的法規風險、經營風險
_REGULATORY_RE = re.compile(r"反壟斷|反托拉斯|壟斷調查|訴訟|官司|調查|監管|法規|罰|制裁|禁令")
_MANAGEMENT_RE = re.compile(r"高管|主管|離職|管理層|接班|內控|執行長")
# 論點摘要裡的 EPS 數字：「2026年預估32塊」「2027年甚至50塊」；中間不能出現股價、目標價這類字，避免把價位當成 EPS
_THESIS_EPS_RE = re.compile(r"(20\d{2})\s*年(?:(?!目標價|股價|價位|營收|市值|配息|股利|股息|現金)[^，。；\d]){0,10}?(\d+(?:\.\d+)?)\s*[元塊]")
# 2026-10-27：v6 prompt 試跑發現，prompt 明寫「不太會受到影響」「AI營收貢獻不大」不要列，模型還是照列，
# 改由程式擋。否定或沒有變化：只說某件事不會發生、影響不大
_NO_CHANGE_RE = re.compile(
    r"不(?:太)?會受(?:到)?.{0,6}(?:影響|衝擊)|不受.{0,6}(?:影響|衝擊)|(?:影響|衝擊)(?:較小|不大|有限)"
    r"|貢獻(?:不大|有限)|抵銷|難以被|不至於")
# 同一段原文另外講了真正的變化(「競爭對手不受關稅影響，科林研發成本轉嫁壓力較大」「傳統業務的下滑」)就不算否定句
_REAL_CHANGE_RE = re.compile(r"壓力|流失|下滑|衰退|減少|增加|增長|成長|提升|上升|下降|漲價|跌價|降價|調漲|調降")
# 泛論：講一整類股票的一般規律，不是這家公司會發生的事(「虧轉盈的股票通常會大漲」)
_GENERAL_RE = re.compile(r"(?:的股票|類股|這類公司|這種公司)(?:通常|往往|一般|多半)")
# 估值合理不是低估：「估值合理」「本益比相對安全」「估值膨脹是正常的」
_FAIR_VALUE_RE = re.compile(r"合理|不貴|相對安全|正常|膨脹")
# 有估值依據的不擋：明說便宜、本益成長比小於 1，或給了倍數(「21-23倍的合理本益比」「淨值比1.1至1.5倍」)
_CHEAP_RE = re.compile(r"低估|便宜|低於|偏低|折價|特價|本益成長比小於|PEG|\d+(?:\.\d+)?\s*倍")
# 「有望推升估值」「估值大幅增加的關鍵」：只說估值會上去，沒有估值依據
_VALUATION_HOPE_RE = re.compile(r"(?:推升|提升|增加|拉高).{0,4}估值|估值.{0,6}(?:推升|提升|增加|拉高)")
_VALUATION_BASIS_RE = re.compile(r"本益比|P/?E|\d+(?:\.\d+)?\s*倍|低估|高估|合理價|股價淨值比|殖利率|便宜|昂貴")
# 單季、半年的 EPS(「預估今年第一季 EPS 6.96 美元」)不能當成全年 EPS 直接指定，不然近四季 EPS 會被設成單季的數字
_PARTIAL_PERIOD_RE = re.compile(r"第\s*[一二三四1-4]\s*季|單季|本季|上季|下季|這一季|[Qq]\s*[1-4]|上半年|下半年|每季")
# 同一句同時講到全年(「上半年已達29元，預計全年有望突破60元」)，AI 抓的通常是全年的數字
_FULL_YEAR_RE = re.compile(r"全年|整年|全年度")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_CN_NUMBER_RE = re.compile(r"[零〇一二兩三四五六七八九十百千萬]+")
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "萬": 10000}


def parse_chinese_number(text: str) -> Optional[int]:
    """
    把節目口語裡的中文數字轉成整數：一百→100、兩千→2000、十五→15、一百零五→105。
    也處理口語省略尾端單位的講法：兩百二→220、一千五→1500、一萬二→12000。
    只有單一個數字字(沒有單位，例如「一」「兩」)回傳 None——那通常是「一家」「兩個」，不是金額。
    """
    if not text or not any(ch in _CN_UNITS for ch in text):
        return None
    total, section, digit = 0, 0, None
    last_unit, prev_was_unit = None, False
    for ch in text:
        if ch in _CN_DIGITS:
            digit = _CN_DIGITS[ch]
            prev_was_unit = False
        else:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                section = (section + (digit or 0)) * unit
                total += section
                section = 0
            else:
                section += (1 if digit is None else digit) * unit
            digit, last_unit, prev_was_unit = None, unit, True
    if digit is not None:
        # 「兩百二」：最後一個數字緊接在單位後面、中間沒有「零」，代表下一位(二十)，不是個位
        if last_unit and last_unit >= 100 and text[-2] in _CN_UNITS:
            section += digit * (last_unit // 10)
        else:
            section += digit
    return total + section


def numbers_in_text(text: str) -> set[float]:
    """
    quote 裡出現的所有數字：阿拉伯數字(含千分位)，以及中文數字。
    「104,105」這種用逗號列舉的數字，除了整串(104105)也拆開各算一個，不然 AI 填的 104 會被誤判成對不到。
    """
    found = set()
    for n in _NUMBER_RE.findall(text or ""):
        found.add(float(n.replace(",", "")))
        if "," in n:
            found.update(float(part) for part in n.split(",") if part and part.replace(".", "", 1).isdigit())
    for n in _CN_NUMBER_RE.findall(text or ""):
        v = parse_chinese_number(n)
        if v is not None:
            found.add(float(v))
    return found
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
    in_quote = numbers_in_text(quote)

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
                 asset_name: str, ticker: str, published_year: Optional[int] = None,
                 thesis: str = "") -> tuple[list, list]:
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
    2026-10-26 抽樣檢查後加的規則：
      8. AI 判斷的利多/利空跟類別方向相反 → 丟掉(多半是否定句，例如「競爭力難以被超越」被分成競爭加劇)
      9. 總經情境依影響管道改分類(需求 → 需求增加/放緩，匯率關稅 → 成本上升)
     10. 財務風險裡的反壟斷、訴訟改成法規風險；高管離職這類經營風險丟掉
     11. 擴產：原文提到海外廠、新廠、折舊、稀釋才標 margin_drag(毛利率才會下降)
     12. 目標價標來源(法人/節目)，沒有數字的丟掉
     13. EPS 預估原文沒有數字、但論點摘要寫了「2026年…32塊」時，從論點摘要補(標 figures_source)
     14. 成本下降依原文標 cost_kind：生產成本(良率、原料)只動毛利率，組織成本(裁員、費用)只動費用率
    2026-10-27(v6 試跑發現 prompt 寫了模型也不照做)：
     15. 否定句、影響不大(「不太會受到影響」「貢獻不大」「有望抵銷」) → 丟掉
     16. 泛論(「虧轉盈的股票通常會大漲」) → 丟掉
     17. 估值偏低的原文只說「合理、相對安全、膨脹是正常的」、也沒有本益比數字 → 丟掉
     18. 估值情境只說「有望推升估值」、沒有估值依據 → 改成 TECHNICAL_FLOW
     19. EPS 預估的原文是單季或半年(「第一季」「Q1」「上半年」) → 數字移到 eps_partial，不能直接指定 EPS
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
        ai_sign = {"up": 1, "down": -1}.get(s["effect"], 0)
        type_sign = lever_effect(s["type"])
        if ai_sign and type_sign and ai_sign != type_sign:
            dropped.append({**s, "reason": "方向跟類別相反"})
            continue
        if s["type"] in ("MACRO_EASING", "MACRO_TIGHTENING"):
            blob = s["quote"] + "".join(s["chain"])
            if not any(a in blob for a in aliases):
                dropped.append({**s, "reason": "總經情境沒有提到這家公司"})
                continue
            sign = ai_sign or (1 if s["type"] == "MACRO_EASING" else -1)
            if _MACRO_DEMAND_RE.search(blob):
                new_type = "DEMAND_UP" if sign > 0 else "DEMAND_DOWN"
                s["fixes"].append(f"{s['type']}→{new_type}(總經透過需求影響)")
                s["type"] = new_type
            elif _MACRO_COST_RE.search(blob) and sign < 0:
                s["fixes"].append(f"{s['type']}→COST_UP(總經透過匯率、關稅、成本影響)")
                s["type"] = "COST_UP"
        if s["type"] == "FINANCIAL_RISK":
            if _MANAGEMENT_RE.search(s["quote"]):
                dropped.append({**s, "reason": "經營風險不參與試算"})
                continue
            if _REGULATORY_RE.search(s["quote"]):
                s["fixes"].append("FINANCIAL_RISK→REGULATORY_RISK(法規風險)")
                s["type"] = "REGULATORY_RISK"
        if s["type"] not in ("TECHNICAL_FLOW", "ANALYST_TARGET") and _NO_CHANGE_RE.search(s["quote"]) \
                and not _REAL_CHANGE_RE.search(s["quote"]):
            dropped.append({**s, "reason": "否定句或影響不大，不是變化"})
            continue
        if _GENERAL_RE.search(s["quote"]):
            dropped.append({**s, "reason": "泛論，不是這家公司的事"})
            continue
        if s["type"] == "RERATING_UP" and _FAIR_VALUE_RE.search(s["quote"]) \
                and not _CHEAP_RE.search(s["quote"]) and not (raw.get("figures") or {}).get("pe"):
            dropped.append({**s, "reason": "估值合理不是低估"})
            continue
        if s["type"] in ("RERATING_UP", "RERATING_DOWN") and _VALUATION_HOPE_RE.search(s["quote"]) \
                and not _VALUATION_BASIS_RE.search(s["quote"]):
            s["fixes"].append(f"{s['type']}→TECHNICAL_FLOW(只說估值會變，沒有估值依據)")
            s["type"] = "TECHNICAL_FLOW"
        if s["type"] in _CURRENT_STATE_TYPES and _CURRENT_STATE_RE.search(s["quote"]) \
                and not _FUTURE_RE.search(s["quote"]):
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
        if s["type"] == "EPS_FORECAST" and s["figures"].get("eps") and _PARTIAL_PERIOD_RE.search(s["quote"]) \
                and not _FULL_YEAR_RE.search(s["quote"]):
            s["figures"]["eps_partial"] = s["figures"].pop("eps")
            s["figures"]["eps"] = None
            s["fixes"].append("單季或半年的 EPS，不能當成全年 EPS 直接指定")
        if s["type"] == "ANALYST_TARGET":
            if not s["figures"].get("target_low"):
                dropped.append({**s, "reason": "目標價沒有數字"})
                continue
            s["source"] = "analyst" if _ANALYST_RE.search(s["quote"]) else "podcast"
        # v6 起由 AI 判斷 margin_drag、cost_kind；v5 的 AI 輸出沒有這兩個欄位，用關鍵字推
        if s["type"] == "CAPACITY_EXPANSION":
            if isinstance(raw.get("margin_drag"), bool):
                s["margin_drag"] = raw["margin_drag"]
            else:
                s["margin_drag"] = bool(_MARGIN_DRAG_RE.search(s["quote"] + "".join(s["chain"])))
        if s["type"] == "COST_EFFICIENCY":
            if raw.get("cost_kind") in ("cogs", "opex", "both"):
                s["cost_kind"] = raw["cost_kind"]
            else:
                blob = s["quote"] + "".join(s["chain"])
                cogs, opex = bool(_COGS_RE.search(blob)), bool(_OPEX_RE.search(blob))
                s["cost_kind"] = "cogs" if cogs and not opex else "opex" if opex and not cogs else "both"
        if raw.get("duration") in ("short", "lasting"):
            s["duration"] = raw["duration"]

        effect = lever_effect(s["type"]) or {"up": 1, "down": -1}.get(s["effect"], 0)
        s["effect"] = {1: "up", -1: "down"}.get(effect, "")
        if thesis_sign and effect:
            s["stance"] = "support" if effect == thesis_sign else "against"
        else:
            s["stance"] = s["effect"] or "unknown"
        kept.append(s)
    return _fill_eps_from_thesis(kept, thesis), dropped


def _fill_eps_from_thesis(kept: list, thesis: str) -> list:
    """
    EPS 預估情境沒有數字，但論點摘要明寫了「2026年預估32塊，2027年甚至50塊」時，用摘要的數字補上：
    第一組填進原本那個情境，其餘各自複製成一個 EPS 預估情境。只補已經存在的 EPS 預估情境，
    不會憑空新增；數字來源標 figures_source="thesis"，畫面上看得出來不是原文直接講的。
    """
    if not thesis:
        return kept
    pairs = [(int(y), float(v)) for y, v in _THESIS_EPS_RE.findall(thesis)]
    if not pairs or not re.search(r"EPS|每股盈餘|獲利", thesis, re.I):
        return kept
    already = {(s["figures"].get("year"), s["figures"].get("eps")) for s in kept if s["type"] == "EPS_FORECAST"}
    pairs = [p for p in pairs if p not in already]
    out = []
    for s in kept:
        out.append(s)
        if s["type"] == "EPS_FORECAST" and not s["figures"].get("eps") and not s["figures"].get("eps_partial") and pairs:
            year, eps = pairs.pop(0)
            s["figures"] = {"eps": eps, "year": year}
            s["figures_source"] = "thesis"
            s["fixes"].append("EPS 數字取自論點摘要")
            for year, eps in pairs:
                out.append({**s, "figures": {"eps": eps, "year": year}, "fixes": list(s["fixes"])})
            pairs = []
    return out


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


def extract_thesis_scenarios(client, model: str, record, prompt_version: str = PROMPT_VERSION) -> dict:
    """
    對一筆 BacktestingRecord 呼叫 Gemini 拆情境，回傳可以直接存進 ThesisScenarioExtraction 的 dict：
      status：ok(有情境) / no_driver(論點太空泛) / failed(呼叫或解析失敗)
      scenarios：檢查、去重後的情境；ai_scenarios：AI 原始輸出(之後改規則可以離線重算)；
      dropped：被檢查丟掉的情境跟原因。
    """
    context_text = build_context(record)
    prompt = PROMPTS[prompt_version].format(
        asset_name=record.asset, ticker=record.ticker, direction=record.direction,
        timeframe=record.timeframe_raw or "(未標示)", thesis=record.thesis,
        evidence_quote=record.evidence_quote or "(無)", context_text=context_text,
        type_list=build_type_list(),
    )
    try:
        # 2.5 Pro 不能關掉思考(thinking_budget=0 會被拒絕)，思考的 token 也算在輸出上限裡，所以放寬
        is_pro = "pro" in model
        resp = gemini_generate_with_retry(
            client=client, model=model, prompt_text=prompt,
            temperature=0.0, max_output_tokens=16000 if is_pro else 4000, max_tries=3,
            thinking_budget=-1 if is_pro else 0,
        )
        obj = json.loads(sanitize_json_text(getattr(resp, "text", "") or ""))
        ai_scenarios = obj.get("scenarios") or []
        if not isinstance(ai_scenarios, list):
            raise ValueError("scenarios 不是陣列")
    except Exception as e:
        return {"status": "failed", "scenarios": [], "ai_scenarios": [], "dropped": [], "unmapped": [],
                "no_driver_reason": "", "error": str(e)[:500]}

    scenarios, dropped = _postprocess(record, ai_scenarios, context_text)
    return {
        "status": "ok" if scenarios else "no_driver",
        "scenarios": scenarios,
        "ai_scenarios": ai_scenarios,
        "dropped": dropped,
        "unmapped": [u for u in (obj.get("unmapped") or []) if isinstance(u, dict)],
        "no_driver_reason": str(obj.get("no_driver_reason") or "")[:500],
        "error": "",
    }


def _postprocess(record, ai_scenarios: list, context_text: str) -> tuple[list, list]:
    """程式端檢查 + 去重，呼叫 Gemini 跟離線重算(recheck_thesis_scenarios)共用同一套。"""
    source_text = f"{record.thesis}\n{record.evidence_quote}\n{context_text}"
    kept, dropped = apply_checks(
        [s for s in ai_scenarios if isinstance(s, dict)], record.direction, source_text,
        record.asset, record.ticker,
        published_year=record.summary.published_at.year if record.summary.published_at else None,
        thesis=record.thesis,
    )
    return dedupe_scenarios(kept), dropped


# ── 第二步：用較強的模型逐一檢查 ─────────────────────────────────────────────
# 2026-10-27：Flash 拆得多(召回高)但常不照規則；3.1 Pro 直接拆時很守規則，卻漏掉一半正確的情境。
# 改成 Flash 先拆、程式檢查後，再請 Pro 判斷每個情境：預設保留，只有明確違反規則才處理，不新增情境。
# 版本演進(舊版 prompt 已刪，內容見 git 歷史)：
#   r1：只能留或刪。41 句上刪掉將近一半，誤刪「持續中的趨勢」和「節目提到的風險」。
#   r2：把上面兩種寫成「一定要保留」，41 句上問題 9→5、正確的 6/6 都保留；40 句外部樣本正確率 89%(v5 75%)。
#   r3：r2 的「現金流被當成財務風險」刪太多(自由現金流轉負、融資壓力是真的財務風險)，改窄。
#   r4(2026-10-28)：Flash 類別分錯、但事件本身成立的(輝達發表新平台被分成需求增加、長榮運力過剩被分成需求放緩)，
#       r3 只能整個刪掉；r4 可以改成正確的類別(retype)。
REVIEW_PROMPT_TEMPLATE = """你是一位嚴謹的證券分析師，負責審查另一位分析師從 Podcast 論點拆出來的「情境」。
這些情境之後會用來試算 {asset_name}（{ticker}）的 營收 → 毛利 → EPS → 股價。

【驗證句】
方向：{direction}
論點：{thesis}
原文引述：{evidence_quote}

【補充段落】
{context_text}

【情境類型清單（改類別時只能從這裡選，填代碼）】
{type_list}

【待審查的情境】（i 是編號）
{scenario_list}

【審查規則】預設保留（keep）。只有「明確」符合下列情況才處理；拿不準就保留。
刪除（drop）：事件本身不成立
A. 泛論：講一整類股票或一般規律，不是 {asset_name} 會發生的事（「虧轉盈的股票通常會大漲」）
B. 否定或沒有變化：只說某件事不會發生、影響不大、不確定（「不太會受影響」「AI營收貢獻不大」「有望抵銷關稅影響」）
C. 靜態的現況：只描述目前「擁有」的優勢、能力、地位、營收結構，或多年前已完成的事
   （「具備成本優勢」「卓越的供應鏈整合能力」「AI伺服器營收佔比高」「多年前併購基美」），沒有說接下來會怎麼變
E. 空泛到沒有具體事件（「長期看好」「值得佈局」「適合波段操作」）
F. 跟另一個情境重複：同一件事已經由另一個情境表達
D. 類別錯、而且清單裡也沒有合適的類別，例如
   - 「有現金流」「現金流穩健」這種正面的現況被當成需求增加；配息率、殖利率高低被當成財務風險
     （注意：自由現金流轉負、現金流因資本支出縮水、需要發債或增資融資，是真的財務風險，要保留）
   - 公司自用的資本支出（蓋自家數據中心）被當成擴產
   - 只說「估值合理」「不貴」「估值膨脹是正常的」被當成估值偏低
   - 漲價、需求的主體是別家公司，不是 {asset_name}
   - 只說「透過AI優化」「提升效率」，沒說哪項成本下降，被當成成本下降
改類別（retype）：事件本身是具體、未來或持續中的變化，只是類別分錯，而且清單裡有明顯更合適的類別，例如
   - 發表、推出新平台或新產品，被分成需求增加 → NEW_BUSINESS
   - 運力或產能供給過剩、導致報價下跌，被分成需求放緩 → PRICE_DOWN
   - 政府或客戶給的價格（支付率、費率）調降，被分成成本上升 → PRICE_DOWN
   只能改成清單裡的代碼，new_type 必填；方向（利多、利空）會依新類別重算。

【以下一定要保留，不可以用 C 或 E 刪除】
- 持續中的趨勢：「出貨持續成長」「訂單能見度高」「銷量持續下滑」「虧損持續擴大」「新製程良率改善中」
  這類描述正在發生、會延續下去的變化，就算是用最近的財報數字來說明，也是情境，要保留。
  只有像「毛利率提升至接近50%」這種已經到位、沒有延續性的單一結果，才算 C。
- 節目提到的風險或反方因素：補充段落裡提到的競爭、法規、需求放緩、成本上升等風險，
  就算驗證句本身沒有提到，也是節目針對這檔股票提到的風險，要保留。
- 產業或客戶的變化會影響 {asset_name} 的（「主要客戶上調資本支出，帶動供應鏈訂單」），要保留。

不要新增情境。

只能輸出合法 JSON，不可包含任何解釋文字或 markdown：
{{"verdicts": [{{"i": <編號>, "action": "keep"|"drop"|"retype", "new_type": "<retype 時填代碼，否則填空字串>",
                "rule": "<drop 時填 A~F，否則填空字串>", "why": "<drop 或 retype 時用一句話說明>"}}]}}
"""
REVIEW_PROMPTS = {"r4": REVIEW_PROMPT_TEMPLATE}

REVIEW_DROP_PREFIX = "Pro 檢查刪除"
REVIEW_RETYPE_PREFIX = "Pro 檢查改類別"
UNMAPPED_REASON = "沒有對應的類別"


def _retype(sc: dict, new_type: str, thesis_direction: str, why: str = "") -> dict:
    """Pro 判斷類別分錯時改成 new_type：方向、立場依新類別重算，只留新類別該有的數字與標記。"""
    out = {k: v for k, v in sc.items() if k not in ("margin_drag", "cost_kind", "review_rule", "review_why")}
    out["type"] = new_type
    effect = lever_effect(new_type) or {"up": 1, "down": -1}.get(sc.get("effect"), 0)
    out["effect"] = {1: "up", -1: "down"}.get(effect, "")
    thesis_sign = {"bullish": 1, "bearish": -1}.get(thesis_direction, 0)
    out["stance"] = ("support" if effect == thesis_sign else "against") if thesis_sign and effect else (out["effect"] or "unknown")
    fields = FIGURE_FIELDS.get(new_type, ())
    out["figures"] = {k: v for k, v in (sc.get("figures") or {}).items() if k in fields}
    blob = out.get("quote", "") + "".join(out.get("chain") or [])
    if new_type == "CAPACITY_EXPANSION":
        out["margin_drag"] = bool(_MARGIN_DRAG_RE.search(blob))
    if new_type == "COST_EFFICIENCY":
        cogs, opex = bool(_COGS_RE.search(blob)), bool(_OPEX_RE.search(blob))
        out["cost_kind"] = "cogs" if cogs and not opex else "opex" if opex and not cogs else "both"
    out["fixes"] = list(sc.get("fixes") or []) + [f"Pro 改類別 {sc.get('type')}→{new_type}" + (f"：{why}" if why else "")]
    return out


def _recheck_retyped(sc: dict) -> Optional[dict]:
    """
    Pro 改類別後，再套一次跟類別有關的程式檢查(apply_checks 是在改類別之前做的)，回傳 None 代表要丟掉。
    2026-10-28 r4 全部重做後發現：Pro 把「股價有機會挑戰 200 元」從技術面改成估值偏低，原文沒有估值依據，
    勾了卻會調本益比；也有改成法人目標價卻沒有數字的。
    """
    t, quote = sc["type"], sc.get("quote", "")
    if t not in ("TECHNICAL_FLOW", "ANALYST_TARGET") and _NO_CHANGE_RE.search(quote) and not _REAL_CHANGE_RE.search(quote):
        return None
    if _GENERAL_RE.search(quote):
        return None
    if t in _CURRENT_STATE_TYPES and _CURRENT_STATE_RE.search(quote) and not _FUTURE_RE.search(quote):
        return None
    if t == "RERATING_UP" and _FAIR_VALUE_RE.search(quote) and not _CHEAP_RE.search(quote) and not sc["figures"].get("pe"):
        return None
    if t in ("RERATING_UP", "RERATING_DOWN") and (not _VALUATION_RE.search(quote) or
                                                 (_VALUATION_HOPE_RE.search(quote) and not _VALUATION_BASIS_RE.search(quote))):
        new_type = "ANALYST_TARGET" if "目標價" in quote else "TECHNICAL_FLOW"
        sc = {**sc, "type": new_type, "figures": {}, "fixes": sc["fixes"] + [f"{t}→{new_type}(沒有估值依據)"]}
        t = new_type
    if t == "ANALYST_TARGET" and not sc["figures"].get("target_low"):
        return None
    return sc


def review_scenarios(client, model: str, record, scenarios: list, context_text: Optional[str] = None,
                     review_version: str = "r4") -> dict:
    """
    第二步檢查：回傳 {"kept": [...], "removed": [{...情境, "review_rule", "review_why"}],
    "retyped": [(原本的情境, 改類別後的情境)], "error": ""}。kept 已經包含改過類別的情境。
    呼叫失敗或回傳格式不對時全部保留(不因為檢查失敗而丟掉情境)。
    """
    if not scenarios:
        return {"kept": [], "removed": [], "retyped": [], "error": ""}
    context_text = build_context(record) if context_text is None else context_text
    listing = "\n".join(
        f"{i}. 類別：{s['type']}（{SCENARIO_TYPES.get(s['type'], {}).get('name', '')}）"
        f"｜{'利多' if s.get('effect') == 'up' else '利空' if s.get('effect') == 'down' else ''}"
        f"｜quote：{s['quote']}｜因果鏈：{' → '.join(s.get('chain') or [])}"
        for i, s in enumerate(scenarios))
    prompt = REVIEW_PROMPTS[review_version].format(
        asset_name=record.asset, ticker=record.ticker, direction=record.direction, thesis=record.thesis,
        evidence_quote=record.evidence_quote or "(無)", context_text=context_text, scenario_list=listing,
        type_list=build_type_list())
    is_pro = "pro" in model
    try:
        resp = gemini_generate_with_retry(
            client=client, model=model, prompt_text=prompt, temperature=0.0,
            max_output_tokens=16000 if is_pro else 4000, max_tries=3, thinking_budget=-1 if is_pro else 0)
        # Pro 偶爾在 JSON 後面多輸出一段(「Extra data」)，只取第一個完整的 JSON 物件
        obj, _ = json.JSONDecoder().raw_decode(sanitize_json_text(getattr(resp, "text", "") or ""))
        verdicts = {int(v["i"]): v for v in obj.get("verdicts") or [] if isinstance(v, dict) and "i" in v}
    except Exception as e:
        return {"kept": list(scenarios), "removed": [], "retyped": [], "error": str(e)[:500]}
    kept, removed, retyped = [], [], []
    for i, s in enumerate(scenarios):
        v = verdicts.get(i) or {}
        action = v.get("action") or ("drop" if v.get("keep") is False else "keep")
        new_type = str(v.get("new_type") or "")
        if action == "drop":
            removed.append({**s, "review_rule": str(v.get("rule") or ""), "review_why": str(v.get("why") or "")})
        elif action == "retype" and new_type in SCENARIO_TYPES and new_type != s["type"]:
            new = _retype(s, new_type, record.direction, str(v.get("why") or ""))
            retyped.append((s, new))       # 紀錄 Pro 的判斷(離線重算照著做)；改完不符合程式檢查的不留
            checked = _recheck_retyped(new)
            if checked is not None:
                kept.append(checked)
        else:
            kept.append(s)
    return {"kept": kept, "removed": removed, "retyped": retyped, "error": ""}


def _review_records(rev: dict) -> list:
    """Pro 的刪除、改類別存進 dropped，離線重算(recheck_thesis_scenarios)時照著重做一次。"""
    removed = [{**sc, "reason": f"{REVIEW_DROP_PREFIX}（{sc.get('review_rule', '')}）{sc.get('review_why', '')}"}
               for sc in rev["removed"]]
    retyped = [{**old, "new_type": new["type"],
                "reason": f"{REVIEW_RETYPE_PREFIX}（{old['type']}→{new['type']}）{new['fixes'][-1].split('：', 1)[-1] if '：' in new['fixes'][-1] else ''}"}
               for old, new in rev["retyped"]]
    return removed + retyped


def _is_review_record(d: dict) -> bool:
    reason = str(d.get("reason", ""))
    return reason.startswith(REVIEW_DROP_PREFIX) or reason.startswith(REVIEW_RETYPE_PREFIX)


def _scenario_key(sc: dict) -> tuple:
    """比對「重算後的情境」是不是 Pro 處理過的那一個：同一句原文可能拆出兩個不同數字的 EPS 預估，數字也要一樣。"""
    figures = {k: v for k, v in (sc.get("figures") or {}).items() if v is not None}
    return sc.get("type"), normalize_text(sc.get("quote", "")), json.dumps(figures, sort_keys=True)


def recheck_thesis_scenarios(record, ai_scenarios: list, topics: Optional[list] = None,
                             prior_dropped: Optional[list] = None) -> dict:
    """
    不呼叫 Gemini，用已經存好的 AI 原始輸出重跑一次程式端檢查——改了檢查規則(例如補上
    中文數字轉換)時用這個重算，不用再花 API 費用、結果也不會因為 AI 重拆而變動。
    回傳的 dict 只有會被檢查影響的欄位(status/scenarios/dropped)。
    topics：同一集的「個股」段落(find_grounded_stock_topics 的結果)，批次重算時同一集
    只查一次再傳進來，不用每一筆驗證句都重查。
    prior_dropped：這一筆原本存的 dropped。兩步做法(v6 起)的 Pro 檢查結果(刪除、改類別)存在裡面，
    重算時照著重做，不然離線重算會把 Pro 處理過的情境還原；「沒有對應的類別」(AI 的 unmapped)也原樣保留。
    """
    scenarios, dropped = _postprocess(record, ai_scenarios or [], build_context(record, topics))
    carried = [d for d in (prior_dropped or []) if _is_review_record(d) or d.get("reason") == UNMAPPED_REASON]
    removed = {_scenario_key(d) for d in carried if str(d.get("reason", "")).startswith(REVIEW_DROP_PREFIX)}
    retypes = {_scenario_key(d): d for d in carried if str(d.get("reason", "")).startswith(REVIEW_RETYPE_PREFIX)}
    out = []
    for sc in scenarios:
        key = _scenario_key(sc)
        if key in removed:
            continue
        if key in retypes:
            d = retypes[key]
            why = d["reason"].split("）", 1)[-1]
            sc = _recheck_retyped(_retype(sc, d["new_type"], record.direction, why))
            if sc is None:
                continue
        out.append(sc)
    return {"status": "ok" if out else "no_driver", "scenarios": out, "dropped": dropped + carried}


def re_review_extraction(row, review_client, review_model: str, review_version: str = REVIEW_VERSION) -> Optional[dict]:
    """
    只重做第二步：用存好的 Flash 原始輸出重跑程式檢查，還原成第一步的情境，再用新版檢查 prompt 重新判斷。
    不重新呼叫 Flash，所以第一步的結果不變。回傳要更新的欄位(status/scenarios/dropped)；
    Pro 呼叫失敗回傳 None(維持原本的結果)。
    """
    record = row.backtest_record
    context = build_context(record)
    first, program_dropped = _postprocess(record, row.ai_scenarios or [], context)
    unmapped = [d for d in row.dropped if d.get("reason") == UNMAPPED_REASON]
    rev = review_scenarios(review_client, review_model, record, first, context, review_version=review_version)
    if rev["error"]:
        return None
    return {"status": "ok" if rev["kept"] else "no_driver", "scenarios": rev["kept"],
            "dropped": program_dropped + _review_records(rev) + unmapped}


def extract_two_step(flash_client, flash_model: str, review_client, review_model: str, record,
                     prompt_version: str = "v6", review_version: str = REVIEW_VERSION) -> dict:
    """
    兩步做法(2026-10-27 起)：Flash 用 prompt_version 拆 → 程式檢查 → review_model 逐一判斷留、刪或改類別。
    41 句問題樣本與 40 句外部樣本驗證過(正確率 v5 75% → 89%，見 out/two_step_fresh_judged.md)。
    回傳格式同 extract_thesis_scenarios，可直接 save_extraction：
      scenarios：Pro 留下的(含改過類別的)；dropped：程式丟掉的 + Pro 的刪除與改類別紀錄 + 沒有對應類別的；
      Pro 檢查失敗時 status=failed(下次批次會整筆重跑)，不存只做了一半的結果。
    """
    first = extract_thesis_scenarios(flash_client, flash_model, record, prompt_version=prompt_version)
    if first["status"] == "failed":
        return first
    unmapped = [{"type": "", "quote": str(u.get("quote") or ""), "driver": str(u.get("driver") or ""),
                 "reason": UNMAPPED_REASON} for u in first.get("unmapped") or []]
    if not first["scenarios"]:
        return {**first, "dropped": first["dropped"] + unmapped}
    rev = review_scenarios(review_client, review_model, record, first["scenarios"], review_version=review_version)
    if rev["error"]:
        return {**first, "status": "failed", "scenarios": [], "error": f"review: {rev['error']}"}
    return {**first, "status": "ok" if rev["kept"] else "no_driver", "scenarios": rev["kept"],
            "dropped": first["dropped"] + _review_records(rev) + unmapped}


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


def save_extraction(record, model: str, result: dict, prompt_version: str = PROMPT_VERSION):
    """把一筆驗證句的萃取結果存進 ThesisScenarioExtraction(批次指令與即時補拆共用)。"""
    from apps.summaries.models import ThesisScenarioExtraction

    return ThesisScenarioExtraction.objects.using("summariesdb").update_or_create(
        backtest_record_id=record.id,
        prompt_version=prompt_version,
        defaults={
            "summary_id": record.summary_id,
            "ticker": record.ticker,
            "asset_name": record.asset,
            "model_name": model,
            "status": result["status"],
            "scenarios": result["scenarios"],
            "ai_scenarios": result["ai_scenarios"],
            "dropped": result["dropped"],
            "no_driver_reason": result["no_driver_reason"],
            "error": result["error"],
        },
    )[0]


MAX_ON_DEMAND_THESES = 5   # 一集一檔最多即時補拆幾句論點，避免單一請求跑太久(同時跑，約 20~40 秒)


def ensure_thesis_extractions(summary_id: int, ticker: str, client=None, model: Optional[str] = None,
                              review_client=None) -> int:
    """
    即時補拆(cache-aside)：這一集這檔股票有驗證句、但還沒有拆好的情境時，當場呼叫 Gemini 拆完並存起來，
    回傳新拆了幾句。批次指令(backfill_thesis_scenarios)只處理跑當下已經存在的驗證句，之後新上架的
    節目要等下一次批次；這裡讓使用者第一次在論點情境試算打開它時就補上，之後都讀存好的結果
    (不會每次打開都重拆，結果才穩定——temperature=0 也不完全穩定)。

    只補「拆了也試算得了」的：個股、不是銀行壽險、有財報資料。重複的論點文字只拆一次。
    v6 起用兩步做法(Flash 拆 → Pro 檢查，每句約 20 秒)，幾句論點同時跑，使用者不用等每一句依序做完。
    """
    import os
    from concurrent.futures import ThreadPoolExecutor
    from django.db import connections
    from apps.summaries.models import BacktestingRecord, FinancialReportCache, ThesisScenarioExtraction
    from .gemini import make_client

    if not is_extractable_ticker(ticker) or ticker in financial_sector_tickers():
        return 0
    if not FinancialReportCache.objects.using("summariesdb").filter(ticker=ticker).exists():
        return 0
    records = list(
        BacktestingRecord.objects.using("summariesdb")
        .filter(summary_id=summary_id, ticker=ticker, summary__mode="pro")
        .exclude(thesis="").select_related("summary").order_by("id")
    )
    done = set(
        ThesisScenarioExtraction.objects.using("summariesdb")
        .filter(backtest_record_id__in=[r.id for r in records], prompt_version=PROMPT_VERSION)
        .exclude(status=ThesisScenarioExtraction.STATUS_FAILED)
        .values_list("backtest_record_id", flat=True)
    )
    todo = [r for r in records if r.id not in done]
    if not todo:
        return 0

    model = model or os.getenv("THESIS_SCENARIO_MODEL", "gemini-2.5-flash")
    client = client or make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
    review_client = review_client or make_client(api_key=os.getenv("GEMINI_API_KEY", ""), location=REVIEW_LOCATION)
    model_name = f"{model}+{REVIEW_MODEL}:{REVIEW_VERSION}"
    by_text = {}
    for r in todo:
        by_text.setdefault(r.thesis.strip(), []).append(r)
    groups = list(by_text.values())[:MAX_ON_DEMAND_THESES]

    def work(group):
        try:
            result = extract_two_step(client, model, review_client, REVIEW_MODEL, group[0],
                                      prompt_version=PROMPT_VERSION, review_version=REVIEW_VERSION)
            for r in group:
                save_extraction(r, model_name, result)
        finally:
            connections["summariesdb"].close()   # 執行緒自己的連線，用完關掉
        return 1

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        return sum(pool.map(work, groups))
