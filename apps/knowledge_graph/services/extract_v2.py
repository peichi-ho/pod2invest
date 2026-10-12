"""
apps/knowledge_graph/services/extract_v2.py

知識圖譜抽取 v2：把原本「一次丟 4 萬字給 LLM，同時找公司、判斷關係、寫理由」
拆成三個可以各自評估的步驟。

  步驟 1｜公司抽取與名稱對照（NER + entity linking）
      逐字稿切成約 3,000 字的小段，LLM 只負責找出「提到哪些公司」與逐字稿裡
      的原始寫法（mention），不判斷關係。小段落讓模型不會漏掉長文中間的內容。
      抽出的名稱用 TickerMap（asset_name / zh_name / ticker）正規化對照成
      正式名稱；mention 必須真的出現在該段原文，否則視為幻覺丟掉。

  步驟 2｜候選公司組合（純規則，不用 LLM）
      兩家公司的 mention 在原文中距離 PAIR_WINDOW_CHARS 字以內，就是一組候選，
      並截取它們一起出現的上下文片段當作判斷依據。

  步驟 3｜逐組判斷關係（relation classification）
      每組只給 LLM 相關片段，四選一：Supply / Co-impact / Substitution / None，
      並附上原文引用句。程式再驗證引用句真的逐字存在於逐字稿，對不上就丟掉，
      取代原本 LLM 二階審查的一部分工作，而且結果是決定性、可重現的。

這個模組只負責「從逐字稿產出 entities + links」，不寫資料庫；
寫入 knowledge_graphdb 由 generate_by_podcast.py 處理。

模型選擇：步驟 1 用 settings.GEMINI_MODEL（flash-lite 就夠，召回率接近 99%）；
步驟 3 預設用 settings.KG_RELATION_MODEL（gemini-2.5-flash）——評估時 flash-lite
做關係判斷過於保守，幾乎全判 None，連線召回率只有 flash 的一半不到。
"""
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from django.conf import settings

from apps.knowledge_graph.generate import _call_gemini_with_retry, _normalize_node_name
from apps.summaries.services.srt import parse_srt

ENTITY_CHUNK_CHARS = 3_000      # 步驟 1 每段字數
ENTITY_CHUNK_OVERLAP_LINES = 3  # 相鄰段落重疊行數，避免公司名剛好被切在段落邊界
PAIR_WINDOW_CHARS = 250         # 步驟 2：兩家公司 mention 距離多近才算一組候選
SNIPPET_PAD_CHARS = 200         # 候選片段在兩個 mention 前後各多取幾個字
MAX_SNIPPETS_PER_PAIR = 3
MAX_PAIRS = 120                 # 單集候選上限（依共同出現次數排序後截斷），控制成本
PAIRS_PER_CALL = 8              # 步驟 3 每次 LLM 呼叫判斷幾組
LLM_WORKERS = 4
# 引用句必須同時包含兩家公司的某個寫法。會漏掉用代名詞指稱的關係（「它的客戶」），
# 但能擋掉大量「引用句跟這兩家公司無關」的腦補連線。
REQUIRE_BOTH_IN_QUOTE = True
MAX_OUTPUT_TOKENS = 8_192

RELATION_TYPES = ["Supply", "Co-impact", "Substitution", "None"]
DEFAULT_RELATION_MODEL = getattr(settings, "KG_RELATION_MODEL", "gemini-2.5-flash")

# 知識圖譜 nodes.industry 的分類（沿用原本 generate_by_podcast prompt 的清單）
INDUSTRIES = [
    "半導體", "電子/硬體", "AI/軟體/雲端", "金融", "ETF/基金", "能源", "汽車/電動車",
    "生技/醫療", "消費/零售", "工業/製造", "媒體/娛樂", "航太/國防", "房地產/建設",
    "電信/網通", "航運/物流", "其他",
]


# ──────────────────────────────────────────────
# 資料結構
# ──────────────────────────────────────────────

@dataclass
class Entity:
    name: str                       # 正式名稱
    ticker: Optional[str] = None
    industry: str = "其他"
    mentions: set[str] = field(default_factory=set)
    positions: list[int] = field(default_factory=list)  # mention 在 plain text 的起始位置


@dataclass
class Transcript:
    lines: list[str]
    text: str                       # 各行以 "\n" 串接，給 LLM 看的版本
    compact: str                    # 去掉所有空白換行，用來驗證引用句

    @classmethod
    def from_srt(cls, srt_content: str) -> "Transcript":
        lines = [it.text for it in parse_srt(srt_content) if it.text]
        text = "\n".join(lines)
        return cls(lines=lines, text=text, compact=_compact(text))


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _call_json(prompt: str, schema: dict, list_key: str, model: Optional[str], stats: dict) -> list[dict]:
    """
    呼叫 Gemini 取回 {list_key: [...]}。輸出被 MAX_OUTPUT_TOKENS 截斷（模型陷入重複迴圈）
    時，盡量救回截斷點之前已經完整的項目。

    API 錯誤（_call_gemini_with_retry 重試用盡，例如 429 配額冷卻超過約 2.4 小時）
    刻意不攔截、直接往上拋：整集放棄、什麼都不寫入，下次執行 process_all_episodes
    時這集仍是「未處理」會自動補跑。如果在這裡吞掉錯誤只略過一段，會把不完整的
    抽取結果寫進資料庫，而且這集之後會被當成已處理，永遠補不回來。
    """
    raw = _call_gemini_with_retry(prompt, response_schema=schema, model=model,
                                  max_output_tokens=MAX_OUTPUT_TOKENS)
    try:
        return json.loads(raw).get(list_key, [])
    except json.JSONDecodeError:
        stats["truncated_output"] = stats.get("truncated_output", 0) + 1
        cut = raw.rfind("}")
        while cut > 0:
            try:
                return json.loads(raw[:cut + 1] + "]}").get(list_key, [])
            except json.JSONDecodeError:
                cut = raw.rfind("}", 0, cut)
        return []


# ──────────────────────────────────────────────
# TickerMap 名稱對照
# ──────────────────────────────────────────────

class CompanyDirectory:
    """
    TickerMap 的正規化別名索引。asset_name / zh_name / ticker 都當別名，
    對應到 ticker；同一個 ticker 的顯示名稱優先用 zh_name（中文通用名）。
    """

    def __init__(self):
        from apps.summaries.models import TickerMap

        rows = list(TickerMap.objects.using("summariesdb").values_list("asset_name", "zh_name", "ticker"))
        self.alias_to_ticker: dict[str, str] = {}
        self.ticker_to_name: dict[str, str] = {}
        for col in (2, 1, 0):  # asset_name 最後寫入、優先權最高
            for row in rows:
                alias = (row[col] or "").strip()
                if len(alias) >= 2 and row[2]:
                    self.alias_to_ticker[_normalize_node_name(alias)] = row[2]
        # 同一個 ticker 可能有多筆別名列（AAPL / Apple / 蘋果），顯示名稱優先取含中文的 zh_name
        has_cjk = re.compile(r"[一-鿿]")
        for asset_name, zh_name, ticker in rows:
            if not ticker:
                continue
            candidate = (zh_name or asset_name or "").strip()
            current = self.ticker_to_name.get(ticker)
            if current is None or (has_cjk.search(candidate) and not has_cjk.search(current)):
                self.ticker_to_name[ticker] = candidate

        # 給步驟 1 prompt 用的提示清單：依長度由長到短掃描，避免短別名誤判
        self._scan_aliases = sorted(
            {(row[c] or "").strip() for row in rows for c in (0, 1)} - {""},
            key=len, reverse=True,
        )

    def resolve(self, *names: str) -> Optional[str]:
        for n in names:
            if n:
                t = self.alias_to_ticker.get(_normalize_node_name(n))
                if t:
                    return t
        return None

    def hints_in(self, text: str) -> list[str]:
        """
        這段文字裡出現的 TickerMap 已知公司，回傳「寫法→正式名稱(ticker)」提示
        （只當 prompt 提示，讓模型 canonical_name 用一致的名稱，不直接當結果）。
        """
        seen, hints = set(), []
        for alias in self._scan_aliases:
            if len(alias) < 2 or alias not in text:
                continue
            ticker = self.resolve(alias)
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            hints.append(f"{alias}→{self.ticker_to_name.get(ticker, alias)}({ticker})")
        return hints[:40]


# ──────────────────────────────────────────────
# 步驟 1：公司抽取
# ──────────────────────────────────────────────

# 讓模型對每個名稱「分類」再由程式只留 company / etf，比要求模型自己「不要輸出」
# 人物、指數、媒體等可靠得多（小模型常常無視排除規則）。
ENTITY_KEEP_KINDS = {"company", "etf"}
ENTITY_KINDS = [
    "company", "etf", "person", "country_or_government", "industry_or_sector",
    "index_or_market", "commodity_or_currency", "media", "financial_research_institution",
    "podcast_or_sponsor", "other",
]

ENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "mention": {"type": "string"},
                    "kind": {"type": "string", "enum": ENTITY_KINDS},
                    "canonical_name": {"type": "string"},
                    "ticker": {"type": "string"},
                    "industry": {"type": "string", "enum": INDUSTRIES},
                },
                "required": ["mention", "kind", "canonical_name"],
            },
        },
    },
    "required": ["companies"],
}

_ENTITY_PROMPT = """你是財經 Podcast 逐字稿的專有名詞標註員。請找出下面這段逐字稿裡提到的所有「公司」，
並把你看到、容易跟公司搞混的其他專有名詞也列出來，用 kind 分類。

【要找的】
- 上市櫃公司（台股、美股、其他市場）、未上市但明確的公司（OpenAI、SpaceX）、ETF
- 只以產品／品牌出現的，算成母公司：ChatGPT→OpenAI、Claude→Anthropic、Gemini→Google、Grok→xAI、iPhone→蘋果
- 逐字稿是語音辨識產生的，公司名可能是同音錯字（例如「台雞店」=台積電），
  只有上下文能明確確定是哪家公司時才抓；一般詞彙不要硬解讀成公司名

【kind 分類】
- company：一般公司（含未上市公司、只以產品／品牌出現時的母公司）
- etf：ETF
- person：人物（川普、孫正義、巴菲特、黃仁勳）——人物一律是 person，不要換成他的公司
- country_or_government：國家地區、政府機關、央行（聯準會、川普政府）
- industry_or_sector：產業、族群、概念股、公司集合名詞（半導體、AI 伺服器、金融股、七巨頭、美股七雄）
- index_or_market：指數或整體市場（加權指數、S&P 500、台股、美股）
- commodity_or_currency：商品、匯率（黃金、原油、美元）
- media：新聞媒體（華爾街日報、福斯新聞、經濟日報）
- financial_research_institution：評等、研究、資料、投資銀行、顧問機構（標普、穆迪、惠譽、彭博、高盛、摩根士丹利、摩根大通、TrendForce、麥肯錫）
- podcast_or_sponsor：節目本身、節目的業配廣告贊助商
- other：其他

【輸出】每個名稱一筆（同一家公司在本段有不同寫法，每種寫法各一筆；同一種寫法只列一次，不要重複）：
- mention：逐字稿中「原封不動」出現的寫法（必須能在下面原文中找到，不可修正錯字）
- canonical_name：正式通用名稱。台股用中文簡稱（台積電、鴻海）；美股用常見中文名，沒有就用英文（輝達、蘋果、Palantir）
- ticker：確定的話填 Yahoo Finance 格式（2330.TW、6488.TWO、NVDA），不確定就留空，不要猜
- industry：company / etf 才需要填，從清單中選最接近的產業

參考：本段出現的已知公司（寫法→正式名稱(ticker)）：{hints}
這些公司的 canonical_name 請直接用箭頭後的正式名稱；不在清單裡的公司也要找。
同一家公司的子品牌、雲端服務、子公司名稱（Azure、AWS、Google Cloud、軟銀集團）canonical_name 一律用母公司正式名稱。

【逐字稿】
{chunk}
"""


def _entity_chunks(lines: list[str]) -> list[str]:
    chunks, current, size = [], [], 0
    for line in lines:
        if current and size + len(line) > ENTITY_CHUNK_CHARS:
            chunks.append("\n".join(current))
            current = current[-ENTITY_CHUNK_OVERLAP_LINES:]
            size = sum(len(x) for x in current)
        current.append(line)
        size += len(line)
    if current:
        chunks.append("\n".join(current))
    return chunks


def _extract_chunk_entities(chunk: str, directory: CompanyDirectory, model: Optional[str],
                            stats: dict) -> list[dict]:
    hints = "、".join(directory.hints_in(chunk)) or "（無）"
    items = _call_json(_ENTITY_PROMPT.format(hints=hints, chunk=chunk), ENTITY_SCHEMA, "companies", model, stats)
    # 只留公司／ETF；mention 必須真的出現在這段原文（擋掉模型自己改寫或幻覺的名稱）
    return [
        it for it in items
        if it.get("kind") in ENTITY_KEEP_KINDS and it.get("mention") and it["mention"] in chunk
    ]


def extract_entities(transcript: Transcript, directory: CompanyDirectory,
                     model: Optional[str] = None, stats: Optional[dict] = None) -> tuple[list[Entity], int]:
    stats = stats if stats is not None else {}
    chunks = _entity_chunks(transcript.lines)
    with ThreadPoolExecutor(max_workers=LLM_WORKERS) as pool:
        results = list(pool.map(lambda c: _extract_chunk_entities(c, directory, model, stats), chunks))

    # 合併：ticker 相同、或任一寫法（正式名稱 / mention）正規化後相同 → 同一家公司。
    # 用 union-find，避免「微軟(MSFT)」「Microsoft（模型這次沒給 ticker）」被拆成兩家。
    records = []
    for items in results:
        for it in items:
            mention, canonical = it["mention"].strip(), (it.get("canonical_name") or "").strip()
            llm_ticker = (it.get("ticker") or "").strip().upper() or None
            ticker = directory.resolve(canonical, mention) or directory.resolve(llm_ticker) or llm_ticker
            records.append((mention, canonical, ticker, it.get("industry") or "其他"))

    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_by_key: dict[str, int] = {}
    for i, (mention, canonical, ticker, _) in enumerate(records):
        keys = {_normalize_node_name(mention), _normalize_node_name(canonical)} - {""}
        if ticker:
            keys.add(f"T:{ticker}")
        for k in keys:
            if k in first_by_key:
                parent[find(i)] = find(first_by_key[k])
            else:
                first_by_key[k] = i

    groups: dict[int, list[tuple]] = defaultdict(list)
    for i, rec in enumerate(records):
        groups[find(i)].append(rec)

    entities = []
    for recs in groups.values():
        ticker = next((r[2] for r in recs if r[2] and r[2] in directory.ticker_to_name), None)             or next((r[2] for r in recs if r[2]), None)
        canonical_votes, industry_votes = defaultdict(int), defaultdict(int)
        for _, canonical, _, industry in recs:
            if canonical:
                canonical_votes[canonical] += 1
            industry_votes[industry] += 1
        name = directory.ticker_to_name.get(ticker) if ticker else None
        name = name or (max(canonical_votes, key=canonical_votes.get) if canonical_votes else recs[0][0])
        entities.append(Entity(
            name=name, ticker=ticker,
            industry=max(industry_votes, key=industry_votes.get),
            mentions={r[0] for r in recs},
        ))

    for ent in entities:
        for m in ent.mentions:
            ent.positions.extend(mt.start() for mt in re.finditer(re.escape(m), transcript.text))
        ent.positions.sort()
    return [e for e in entities if e.positions], len(chunks)


# ──────────────────────────────────────────────
# 步驟 2：候選公司組合
# ──────────────────────────────────────────────

@dataclass
class CandidatePair:
    a: Entity
    b: Entity
    snippets: list[str]
    cooccurrences: int


def build_candidate_pairs(entities: list[Entity], transcript: Transcript) -> list[CandidatePair]:
    text = transcript.text
    pairs = []
    for i, a in enumerate(entities):
        for b in entities[i + 1:]:
            spans = []
            for pa in a.positions:
                for pb in b.positions:
                    if abs(pa - pb) <= PAIR_WINDOW_CHARS:
                        spans.append((min(pa, pb), max(pa, pb)))
            if not spans:
                continue
            # 距離越近的共同出現越有可能是在講兩者的關係，優先取用；重疊的片段合併
            spans.sort(key=lambda s: s[1] - s[0])
            windows: list[list[int]] = []
            for s, e in spans:
                lo, hi = max(0, s - SNIPPET_PAD_CHARS), min(len(text), e + SNIPPET_PAD_CHARS)
                for w in windows:
                    if lo <= w[1] and hi >= w[0]:
                        w[0], w[1] = min(w[0], lo), max(w[1], hi)
                        break
                else:
                    windows.append([lo, hi])
                if len(windows) >= MAX_SNIPPETS_PER_PAIR:
                    break
            snippets = [text[lo:hi] for lo, hi in sorted(windows)]
            pairs.append(CandidatePair(a=a, b=b, snippets=snippets, cooccurrences=len(spans)))

    pairs.sort(key=lambda p: -p.cooccurrences)
    return pairs[:MAX_PAIRS]


# ──────────────────────────────────────────────
# 步驟 3：關係判斷
# ──────────────────────────────────────────────

RELATION_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pair_id": {"type": "integer"},
                    "relation_type": {"type": "string", "enum": RELATION_TYPES},
                    "supplier": {"type": "string", "enum": ["A", "B", "none"]},
                    "event": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium"]},
                },
                "required": ["pair_id", "relation_type"],
            },
        },
    },
    "required": ["results"],
}

_RELATION_PROMPT = """你是產業鏈分析師。下面每一組是同一集財經 Podcast 逐字稿裡「距離很近、一起被提到」的兩家公司 A、B，
附上它們一起出現的原文片段。請只根據片段內容，判斷主持人是否「明確講到」A 與 B 之間的關係。

【四選一】
- Supply：一方提供產品、零組件、代工、服務、授權給另一方。supplier 填供應方是 A 還是 B（非 Supply 填 none）。
  ✅「台積電幫輝達代工」✅「緯穎的伺服器主要賣給 Meta」❌「兩家都在 AI 供應鏈」
- Co-impact：兩者因「同一個具體事件或因素」一起受益或受害，event 寫出是哪個事件。
  事件可以是總體因素（台幣升值、關稅、原料短缺），但片段必須講到這個事件「如何影響這兩家公司的營運」。
  ✅「微軟砍單，廣達跟緯創都受影響」
  ✅「中東石化設施受損，台化、台塑都進入缺料模式」
  ✅「美國撤銷三星、SK海力士、Intel 從大陸採購設備的豁免」（同一個政策直接點名這幾家）
  ❌「AI 熱潮這些公司都會受惠」（趨勢，不是具體事件）
  ❌「兩家都是金控股」「像是 Alphabet、Meta、微軟這些超大規模運算商」（只是被歸在同一類）
  ❌ 股價／盤勢報導：「輝達、博通、甲骨文都跌 3%，微軟、蘋果、Meta 也下跌」「輝達、博通、AMD 股價都很好，因為 GPU 供不應求」
     ——多家公司在同一句被列舉一起漲跌，就算有附帶大盤或產業原因，一律 None
- Substitution：兩者在同一市場搶同一群客戶／訂單，或一方產品取代另一方。
  ✅「AMD 的 MI300 在搶輝達的訂單」✅「華為昇騰晶片取代輝達在中國的市場」
  ❌ 只是同產業 ❌「Anthropic 跟 Google 下禮拜都要發表新模型」（都在做同一件事 ≠ 互搶）
  ❌「AMD、Intel 這些輝達的競爭對手」→ AMD↔Intel 是 None（兩者都是第三家的對手，不代表它們互搶；但 AMD↔輝達、Intel↔輝達 可以是 Substitution）
- None：以上皆非。包括：只是一起被列舉、同產業、用常識才推得出來但片段沒講、
  假設性情境（「如果／假設／若…的話」「可能會」）、投資／入股／併購／持股／出售持股／母子公司關係
  （這類一律 None，不要歸成 Supply）

【規則】
- 寧可判 None 也不要用常識腦補；片段沒講的關係就是 None
- 不是 None 時，evidence_quote 必須從片段中「逐字複製」10–80 字的連續原文（可以跨行，但不可改寫、不可加字），
  而且必須同時包含 A 和 B 的名稱（用逐字稿寫法）；找不到同時提到兩家、又能說明關係的句子，就判 None
- reason 用一句話（15–50 字）寫出具體發生了什麼
- confidence：講得很明確填 high；需要一點上下文推論填 medium

{pairs}
"""


def _format_pairs(batch: list[tuple[int, CandidatePair]]) -> str:
    blocks = []
    for pid, p in batch:
        snippets = "\n---\n".join(p.snippets)
        blocks.append(
            f"===== pair_id={pid} =====\n"
            f"A：{p.a.name}（逐字稿寫法：{'、'.join(sorted(p.a.mentions))}）\n"
            f"B：{p.b.name}（逐字稿寫法：{'、'.join(sorted(p.b.mentions))}）\n"
            f"片段：\n{snippets}"
        )
    return "\n\n".join(blocks)


def classify_pairs(pairs: list[CandidatePair], transcript: Transcript,
                   model: Optional[str] = None) -> tuple[list[dict], dict]:
    indexed = list(enumerate(pairs))
    batches = [indexed[i:i + PAIRS_PER_CALL] for i in range(0, len(indexed), PAIRS_PER_CALL)]

    stats = defaultdict(int)

    def run(batch):
        return _call_json(_RELATION_PROMPT.format(pairs=_format_pairs(batch)), RELATION_SCHEMA, "results", model, stats)

    with ThreadPoolExecutor(max_workers=LLM_WORKERS) as pool:
        results = [r for rs in pool.map(run, batches) for r in rs]

    links = []
    for r in results:
        pid = r.get("pair_id")
        if not isinstance(pid, int) or not 0 <= pid < len(pairs):
            stats["bad_pair_id"] += 1
            continue
        rt = r.get("relation_type")
        if rt not in RELATION_TYPES or rt == "None":
            stats["none"] += 1
            continue
        quote = (r.get("evidence_quote") or "").strip()
        # 引用句必須逐字存在於逐字稿（忽略空白換行差異），否則視為模型腦補
        if not 6 <= len(_compact(quote)) <= 120 or _compact(quote) not in transcript.compact:
            stats["dropped_bad_quote"] += 1
            continue

        p = pairs[pid]
        if REQUIRE_BOTH_IN_QUOTE and not (
            any(_compact(m) in _compact(quote) for m in p.a.mentions)
            and any(_compact(m) in _compact(quote) for m in p.b.mentions)
        ):
            stats["dropped_quote_missing_entity"] += 1
            continue
        source, target = p.a, p.b
        if rt == "Supply":
            if r.get("supplier") == "B":
                source, target = p.b, p.a
            elif r.get("supplier") != "A":
                stats["dropped_no_direction"] += 1
                continue
        stats[rt] += 1
        links.append({
            "source": source.name,
            "target": target.name,
            "relation_type": rt,
            "reason": (r.get("reason") or "").strip(),
            "event": (r.get("event") or "").strip(),
            "evidence_quote": quote,
            "confidence": r.get("confidence") or "medium",
        })
    stats["llm_calls"] = len(batches)
    return links, dict(stats)


# ──────────────────────────────────────────────
# 整合
# ──────────────────────────────────────────────

def extract_graph_v2(
    srt_content: str,
    directory: Optional[CompanyDirectory] = None,
    entity_model: Optional[str] = None,
    relation_model: Optional[str] = DEFAULT_RELATION_MODEL,
) -> dict:
    """
    回傳：
        {
            "entities": [{"name", "ticker", "industry", "mentions", "mention_count"}],
            "links": [{"source", "target", "relation_type", "reason", "event",
                       "evidence_quote", "confidence"}],
            "stats": {...},
        }
    """
    directory = directory or CompanyDirectory()
    transcript = Transcript.from_srt(srt_content)
    if not transcript.lines:
        return {"entities": [], "links": [], "stats": {"empty_transcript": True}}

    entity_stats: dict = {}
    entities, entity_calls = extract_entities(transcript, directory, model=entity_model, stats=entity_stats)
    pairs = build_candidate_pairs(entities, transcript)
    links, rel_stats = classify_pairs(pairs, transcript, model=relation_model) if pairs else ([], {})

    return {
        "entities": [
            {"name": e.name, "ticker": e.ticker, "industry": e.industry,
             "mentions": sorted(e.mentions), "mention_count": len(e.positions)}
            for e in sorted(entities, key=lambda e: -len(e.positions))
        ],
        "links": links,
        "stats": {
            "entity_llm_calls": entity_calls, "candidate_pairs": len(pairs),
            **{f"entity_{k}": v for k, v in entity_stats.items()}, **rel_stats,
        },
    }
