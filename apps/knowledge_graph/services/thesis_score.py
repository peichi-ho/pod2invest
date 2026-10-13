"""
apps/knowledge_graph/services/thesis_score.py

用既有知識圖譜（relation_type: Supply / Co-impact / Substitution）
為投資句（BacktestingRecord.thesis）打一個 0~100 的信心分數。

規則：
    - 以 asset 為錨點，抓 [start_time - 7天, start_time]（含當天）內、
      knowledge_graphdb.links 中 source 或 target 為該 asset 的相關連線；
      7 天內完全沒有可用連線時，放寬到 14 天再查一次
    - 排除跟這句投資句同一個 podcast_source 的連線（避免自我佐證）
    - 先依 (鄰居, relation_type) 分組，組內再用 LLM 判斷 reason 是否語意相近，
      聚類出「獨立事實」（同一件事被重複報導 vs 不同的事各自獨立）
    - 每個獨立事實依重複次數 n 用 1+ln(n) 做收斂加權，避免：
        (a) 完全不去重 → 熱門股被大量重複報導的同一件事灌爆分數
        (b) 直接去重成 1 → 抹煞掉「多方獨立佐證」應該更有份量的訊號
    - 用 sigmoid 把加權總和映射到 0~100，raw=0（查無資料或正負抵銷）時為中性 50 分

方向感知（B1）：
    relation_type 本身只描述「結構關係」，不代表利多利空——一條 Supply 連線
    如果鄰居自己正在被看空，對「做多本股」這句話其實是壞消息，不是好消息。
    因此把每個 relation_type 拆成「量級」(magnitude) 和「同向/反向」(polarity)：
        Supply / Co-impact  → polarity=+1（同向共振：鄰居漲，通常對本股也是利多佐證）
        Substitution        → polarity=-1（零和替代：鄰居漲，代表本股正在被搶市占）
    再用鄰居自己在同一時間窗內的 StockSentimentScore.macro_score 平均值
    （-1~1，來自摘要 pipeline 對「個股：X」段落的 AI 分類）當作鄰居方向，
    乘上 thesis 本身的方向（bullish=+1 / bearish=-1）與 polarity，
    決定這條證據對「這句話」到底是佐證還是反證。
    查不到鄰居方向（例如鄰居是產業節點、或鄰居沒有 StockSentimentScore 資料）時，
    退回舊版的固定符號權重（LEGACY_SIGNED_WEIGHTS），不強行猜測方向。

驗證句內容判斷（B4，優先於 B1）：
    B1 只看「標的 + 時間 + 方向」，同一週同一標的、同方向的不同驗證句會拿到
    幾乎相同的分數（證據集合一樣）。B4 把驗證句本身連同所有證據一次交給 LLM，
    逐條判斷這條證據對「這句話」是 support / contradict / unrelated（strong / weak），
    用判斷結果取代 B1 的方向推估；unrelated 的證據貢獻為 0，熱門股不會因為
    相關新聞多就被灌分。LLM 呼叫失敗時退回 B1 / legacy，不讓分數變空。

說話者準確率（B5）：
    說出這句話的講者自己的歷史準確率，跟「同一時間點全體講者的平均準確率」
    比較，換算成加減分直接加到 raw 上（比平均準往上、比平均差往下，跟證據
    方向無關）。基準不用固定 50%：整體看多的驗證句本來就比較容易過關，
    多數講者準確率都在 65%~70%，用 50% 當基準會變成人人加分。
    只用 start_time 之前已經結算（end_time < start_time）的回測紀錄計算，
    避免用到未來資訊；樣本少時用 n/(n+K) 往基準收縮，不會因為兩三筆紀錄
    就大幅加減分。

來源可信度（B3）：
    links.podcast_source 只是文字（不是外鍵），需要反查回 SummaryRecord.podcaster
    才能對到 SpeakerAccuracy（講者歷史準確率）。查得到、且歷史樣本數足夠時，
    用準確率把這條證據的權重放大或縮小（clamp 在 0.5~1.5 倍，避免單一講者
    的可信度把單一證據放大到蓋過其他證據）；查不到、或樣本數太少（冷啟動）
    一律視為中性（1.0 倍），不懲罰也不加分。

目前只是實驗腳本，尚未寫回資料庫欄位；用 debug_sample() 挑幾筆真實投資句試跑。
"""
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from django.conf import settings
from django.db import connections
from django.db.utils import InterfaceError, OperationalError

from apps.knowledge_graph.generate import _call_gemini_with_retry, _normalize_node_name
from apps.podcasts.models import Podcast, PodcastEpisode
from apps.summaries.models import SpeakerAccuracy, StockSentimentScore, SummaryRecord
from apps.summaries.services.backtesting import (
    COMMODITY_TICKER_MAP,
    INDEX_TICKER_MAP,
    _rule_based_ticker,
    resolve_ticker,
)

# 見 _build_asset_alias_index() 的說明：這兩個 map 解析出來的 ticker，
# 拿來幫知識圖譜 node 分組時需要額外做嚴格比對，不能直接信任子字串比對。
_INDEX_COMMODITY_TICKERS = set(INDEX_TICKER_MAP.values()) | set(COMMODITY_TICKER_MAP.values())
_GENERIC_NAME_SUFFIXES = ("指數", "ETF", "基金", "類股")


def _is_exact_index_match(name: str, ticker: str) -> bool:
    """
    去掉常見字尾（指數/ETF/基金/類股）後，name 是否整串完全等於
    INDEX_TICKER_MAP / COMMODITY_TICKER_MAP 裡對應這個 ticker 的某個關鍵字
    （而不是「關鍵字是 name 的子字串」）。用來過濾掉子字串比對誤判的案例，
    例如 core="標普500金融服務類股"（去掉「指數」後）不等於任何關鍵字，
    就不會被誤判成 S&P 500 指數本身。
    """
    core = name
    for suf in _GENERIC_NAME_SUFFIXES:
        if core.endswith(suf):
            core = core[: -len(suf)]
            break
    for keys, mapped_ticker in {**INDEX_TICKER_MAP, **COMMODITY_TICKER_MAP}.items():
        if mapped_ticker == ticker and (name in keys or core in keys):
            return True
    return False


# relation_type → 量級（不帶符號，符號改由 polarity × 方向決定，見 B1）
MAGNITUDES = {
    "Supply": 15,
    "Co-impact": 10,
    "Substitution": 20,
}

# relation_type → 同向(+1) / 反向(-1)。Co-impact/Supply 是共同受益的同向關係；
# Substitution 是零和競爭，鄰居的方向跟本股的方向是相反的。
RELATION_POLARITY = {
    "Supply": 1,
    "Co-impact": 1,
    "Substitution": -1,
}

# 查不到鄰居方向、或沒給 thesis direction 時，退回這組舊版固定符號權重
# （等同於原本沒有方向感知之前的行為，維持向後相容）。
LEGACY_SIGNED_WEIGHTS = {
    "Supply": 15,
    "Co-impact": 10,
    "Substitution": -20,
}

# BacktestingRecord.direction 的字面值 → 數值方向
DIRECTION_SIGN = {"bullish": 1, "bearish": -1}

WINDOW_DAYS = 7        # 往前抓幾天（含當天）
FALLBACK_WINDOW_DAYS = 14  # WINDOW_DAYS 內完全沒有可用連線時，放寬再查一次的天數
SIGMOID_SCALE = 100.0  # 越小分數對 raw 越敏感，越大越平緩

# 講者可信度：樣本數太少（冷啟動）時準確率還不可靠，視為中性
CREDIBILITY_MIN_SAMPLE = 5
# 準確率落在此值時 multiplier = 1.0（不調整）
CREDIBILITY_BASELINE = 0.5
# 準確率每偏離 baseline 1.0，multiplier 偏離 1.0 多少
CREDIBILITY_SENSITIVITY = 1.0
# multiplier 上下限，避免單一講者的可信度把單一證據放大/縮小到不成比例
CREDIBILITY_CLAMP = (0.5, 1.5)

# B4：LLM 對「這條證據 vs 這句驗證句」的判斷 → 符號與強度
STANCE_SIGN = {"support": 1, "contradict": -1, "unrelated": 0}
STRENGTH_WEIGHT = {"strong": 1.0, "weak": 0.5}
# 預設用 settings.GEMINI_MODEL；需要時可用 THESIS_JUDGE_MODEL 換成較強的模型
THESIS_JUDGE_MODEL = getattr(settings, "THESIS_JUDGE_MODEL", None)

# B5：說話者準確率加減分。比全體平均準確率高 25 個百分點（且樣本夠多）時
# raw +SPEAKER_WEIGHT，低 25 個百分點時 -SPEAKER_WEIGHT；量級約等於一條強支持的 Supply 證據。
SPEAKER_WEIGHT = 15.0
SPEAKER_FULL_SCALE_GAP = 0.25
SPEAKER_MIN_SAMPLE = CREDIBILITY_MIN_SAMPLE
# 收縮強度：樣本數 n 時只採信 n/(n+K) 的偏離量，n=10 時採信一半
SPEAKER_SHRINK_K = 10


def fetch_related_links(
    asset: str,
    start_time: date,
    exclude_podcast_source=None,
    window_days: int = WINDOW_DAYS,
    asset_aliases: Optional[list[str]] = None,
) -> list[dict]:
    """
    撈出 [start_time - window_days, start_time]（含當天）內，
    與 asset 相關（source 或 target 為 asset）、relation_type 屬於 MAGNITUDES 的
    所有 knowledge_graph links。排除 exclude_podcast_source（通常是這句投資句
    自己那一集），避免自我佐證。可以給單一字串或字串清單——同一集在新舊
    兩條知識圖譜管線裡的 podcast_source 格式不同（見 _resolve_podcaster），
    兩種都要排除。

    asset_aliases：跟 asset 同一個實際資產、但在知識圖譜裡可能寫法不同的
    node 名稱清單（見 _resolve_asset_aliases()），例如 asset="S&P 500指數"
    在知識圖譜裡可能是以「標普500指數」或「S&P500」存在。不給的話退回只用
    asset 原字串精確比對（等同於加這個機制之前的行為）。
    """
    window_start = start_time - timedelta(days=window_days)
    relation_types = tuple(MAGNITUDES.keys())
    names = asset_aliases if asset_aliases else [asset]

    with connections["knowledge_graphdb"].cursor() as cursor:
        cursor.execute(
            f"""
            SELECT source, target, relation_type, reason, summary_date, podcast_source
            FROM links
            WHERE (source = ANY(%s) OR target = ANY(%s))
              AND summary_date BETWEEN %s AND %s
              AND relation_type IN ({",".join(["%s"] * len(relation_types))})
            """,
            [names, names, window_start, start_time, *relation_types],
        )
        rows = cursor.fetchall()

    names_set = set(names)
    if isinstance(exclude_podcast_source, str):
        excluded = {exclude_podcast_source}
    else:
        excluded = set(exclude_podcast_source or [])
    links = []
    for source, target, relation_type, reason, summary_date, podcast_source in rows:
        if podcast_source in excluded:
            continue
        asset_is_source = source in names_set
        neighbor = target if asset_is_source else source
        links.append({
            "neighbor": neighbor,
            "asset_is_source": asset_is_source,
            "relation_type": relation_type,
            "reason": reason,
            "summary_date": summary_date,
            "podcast_source": podcast_source,
        })
    return links


@dataclass
class AssetAliasIndex:
    """_build_asset_alias_index() 的結果，整批算分只建一次。"""
    # {ticker: [知識圖譜裡解析到這個 ticker 的所有 node 名稱]}
    ticker_to_nodes: dict[str, list[str]]
    # {正規化名稱: [知識圖譜裡正規化後相同的所有 node 名稱]}，處理大小寫/全半形/空白差異
    norm_to_nodes: dict[str, list[str]]
    # {正規化別名: ticker}，別名來自 TickerMap 的 asset_name / zh_name / ticker 三欄
    ticker_map_norm_index: dict[str, str]


def _build_ticker_map_norm_index() -> dict[str, str]:
    """
    TickerMap 的 asset_name / zh_name / ticker 三欄都當成別名，正規化
    （全形轉半形、去空白、轉小寫，見 _normalize_node_name）後對應到 ticker。

    原本只用 asset_name 整串精確比對，大小寫不同（"CoreWeave" vs 表裡的
    "Coreweave"）或 node 用的是 zh_name 那種寫法（"開發金控"）都會對不到。
    asset_name 最後寫入、優先權最高；長度不到 2 的別名略過，避免單字母誤判。
    """
    from apps.summaries.models import TickerMap

    rows = list(
        TickerMap.objects.using("summariesdb").values_list("asset_name", "zh_name", "ticker")
    )
    index: dict[str, str] = {}
    for col in (2, 1, 0):  # ticker → zh_name → asset_name，後寫的覆蓋先寫的
        for row in rows:
            alias = (row[col] or "").strip()
            if len(alias) < 2 or not row[2]:
                continue
            index[_normalize_node_name(alias)] = row[2]
    return index


def _build_asset_alias_index() -> AssetAliasIndex:
    """
    把知識圖譜目前所有 node 名稱，各自解析成 ticker，分組成
    {ticker: [對應同一個資產的所有不同寫法]}，同時建立正規化名稱分組。

    用來解決 BacktestingRecord.asset 跟知識圖譜 node 名稱是兩條沒對齊的
    命名系統的問題——同一個資產常常有不同寫法（"S&P 500指數" vs
    "標普500指數" vs "S&P500"），直接拿 asset 原字串去精確比對
    links.source/target 常常因為拼法不同而查不到，不是真的沒有證據。

    跟 resolve_ticker()（apps/summaries/services/backtesting.py）用同一套
    解析邏輯（規則優先，查不到才退回 TickerMap），確保跟 asset 的解析結果
    是同一套標準——差別是 resolve_ticker() 對 TickerMap 沒命中規則時，
    每次都各查一次資料庫；這裡 node 數量可能有幾千筆，逐一查會很慢，改成
    先一次把整張 TickerMap 撈進記憶體查表。只在每次批次執行開始時建立
    一次（不適合每筆 BacktestingRecord 都重建一次）。
    """
    with connections["knowledge_graphdb"].cursor() as cursor:
        cursor.execute("SELECT DISTINCT name FROM nodes")
        names = [row[0] for row in cursor.fetchall()]

    ticker_map_norm_index = _build_ticker_map_norm_index()

    grouped: dict[str, list[str]] = defaultdict(list)
    norm_to_nodes: dict[str, list[str]] = defaultdict(list)
    for name in names:
        norm_to_nodes[_normalize_node_name(name)].append(name)
        ticker = _rule_based_ticker(name) or ticker_map_norm_index.get(_normalize_node_name(name))
        if not ticker:
            continue
        # resolve_ticker() 對指數／大宗商品是用「關鍵字是不是 asset 的子字串」
        # 判斷（給單一句子用沒問題，例如「標普」一詞在對話裡幾乎都指 S&P 500）；
        # 但知識圖譜的 node 名稱常常是更長、更具體的專有名詞，子字串比對會
        # 把不相干的東西也掃進來——例如「標普500金融服務類股指數」是產業
        # 子指數、「新光標普電動車ETF」是完全不同的 ETF、「標普全球」是
        # 公司（ticker SPGI）不是指數本身，這三個都會因為含有「標普」被誤判
        # 成 ^GSPC。只有落在這個風險範圍內的 ticker（指數/大宗商品）才需要
        # 用 _is_exact_index_match() 再嚴格比對一次；股票代號、公司別名、
        # TickerMap 精確比對這幾種解析路徑本身就是整串相等，沒有這個風險。
        if ticker in _INDEX_COMMODITY_TICKERS and not _is_exact_index_match(name, ticker):
            continue
        grouped[ticker].append(name)
    return AssetAliasIndex(
        ticker_to_nodes=dict(grouped),
        norm_to_nodes=dict(norm_to_nodes),
        ticker_map_norm_index=ticker_map_norm_index,
    )


def _resolve_asset_aliases(asset: str, alias_index: AssetAliasIndex) -> list[str]:
    """
    把 BacktestingRecord.asset 解析成一組「在知識圖譜裡可能出現的名稱」。

    一定包含 asset 原字串本身（以防它剛好就是某個 node 的正式名稱，維持
    原本精確比對還能命中的案例），再加上：
    - 正規化後跟 asset 相同的 node（只差大小寫/全半形/空白，例如 "NIKE" vs "Nike"），
      asset 解析不出 ticker（OpenAI、SpaceX…）時也適用
    - asset 能解析出 ticker 的話，知識圖譜裡所有解析到同一個 ticker 的 node
      （resolve_ticker 查不到時，再用 TickerMap 三欄正規化別名補查一次）
    """
    norm = _normalize_node_name(asset)
    variants = {asset}
    variants.update(alias_index.norm_to_nodes.get(norm, []))
    ticker = resolve_ticker(asset) or alias_index.ticker_map_norm_index.get(norm)
    if ticker:
        variants.update(alias_index.ticker_to_nodes.get(ticker, []))
    return list(variants)


def _is_similar_reason(reason1: str, reason2: str) -> bool:
    """
    用 LLM 判斷兩段 reason 是否在講同一件事，只回傳 YES/NO。

    改用 generate.py 的 _call_gemini_with_retry（跟知識圖譜抽取共用同一套
    429 重試邏輯），配額耗盡時會自動睡過冷卻期（最長約 2.4 小時）再重試。
    這裡不能只是「失敗就當作不相似」草草放過：這個函式跑在批次算分的
    聚類迴圈裡，如果配額限制持續一段時間，原本每次直接失敗會讓大量本來
    相似的 reason 被誤判成互不相關的獨立事實，不是單純變慢，而是悄悄
    把 fact_count 灌水、扭曲分數本身——所以要先把配額限制的重試撐過去，
    真的重試次數用盡才退回「視為不相似」。
    """
    prompt = (
        "以下兩段描述，是否在表達相同或高度相似的商業事實？請只回答 YES 或 NO，不要解釋。\n\n"
        f"描述一：{reason1}\n"
        f"描述二：{reason2}"
    )
    try:
        result = _call_gemini_with_retry(prompt)
        return result.strip().upper().startswith("YES")
    except Exception as e:
        print(f"⚠️ reason 比對失敗（重試已耗盡），視為不相似。錯誤：{e}")
        return False


def _cluster_reasons(links: list[dict]) -> list[list[dict]]:
    """
    貪婪聚類：同一個 (鄰居, relation_type) 群組裡的多筆 link，
    語意相近的 reason 視為「同一件事被重複報導」，聚成一類。

    回傳每個聚類包含的原始 link dict 清單（而不是只回傳筆數），
    是為了保留每筆證據的 podcast_source，供可信度加權使用（B3）。

    例如某群組有 5 筆 link，其中 3 筆講同一件事、2 筆講另一件事，
    回傳 [[l1,l2,l3], [l4,l5]]，代表這一組其實是兩個獨立事實，不是一件事。
    """
    clusters: list[list[dict]] = []
    for link in links:
        matched = False
        for cluster in clusters:
            if _is_similar_reason(link["reason"], cluster[0]["reason"]):
                cluster.append(link)
                matched = True
                break
        if not matched:
            clusters.append([link])
    return clusters


def _sigmoid_score(raw: float, scale: float = SIGMOID_SCALE) -> float:
    """raw=0 -> 50 分；正數往 100 靠近，負數往 0 靠近。"""
    return 100 / (1 + math.exp(-raw / scale))


# ──────────────────────────────────────────────
# B1：鄰居自己的方向（同一時間窗內的 StockSentimentScore 平均 macro_score）
# ──────────────────────────────────────────────

def _neighbor_direction(neighbor: str, window_start: date, window_end: date) -> Optional[float]:
    """
    鄰居公司自己在 [window_start, window_end] 內的情緒方向，取
    StockSentimentScore.macro_score（-1 悲觀 / 0 中性 / +1 樂觀）平均值。

    回傳 None 代表「不知道鄰居的方向」（鄰居可能是產業節點沒有 ticker、
    或這段期間剛好沒有任何一集節目對這支股票做過分類），呼叫端應該退回
    舊版固定符號權重，而不是把 None 當成 0（0 代表『看法中性偏向不明朗』，
    語意上跟『我們沒有資料判斷』不一樣，不該混用）。
    """
    ticker = resolve_ticker(neighbor)
    if not ticker:
        return None

    scores = list(
        StockSentimentScore.objects.using("summariesdb")
        .filter(
            ticker=ticker,
            summary__published_at__date__gte=window_start,
            summary__published_at__date__lte=window_end,
        )
        .values_list("macro_score", flat=True)
    )
    if not scores:
        return None
    return sum(scores) / len(scores)


# ──────────────────────────────────────────────
# B3：來源可信度（反查 podcast_source → podcaster → SpeakerAccuracy）
# ──────────────────────────────────────────────

def _all_show_names() -> list[str]:
    return list(Podcast.objects.using("podcasts").values_list("show_name", flat=True))


def _resolve_podcaster(podcast_source: str, show_names: list[str]) -> Optional[str]:
    """
    把 links.podcast_source（純文字，不是外鍵）反查回 SummaryRecord.podcaster，
    才能查 SpeakerAccuracy。podcast_source 有兩種歷史格式：
        - generate.py（舊管線）           → SummaryRecord.source_filename
        - generate_by_podcast.py（新管線） → f"{show_name} - {episode_title}"
    兩種都試，都查不到就回傳 None（呼叫端視為中性可信度，不調整權重）。

    注意：這是靠字串比對反查，不是真正的外鍵關聯，屬於權宜作法——
    如果未來要長期依賴可信度加權，建議在 links 表直接存 episode_id，
    才不用每次都靠格式猜測反查。
    """
    podcaster = (
        SummaryRecord.objects.using("summariesdb")
        .filter(source_filename=podcast_source)
        .exclude(podcaster="")
        .values_list("podcaster", flat=True)
        .first()
    )
    if podcaster:
        return podcaster

    for show_name in show_names:
        prefix = f"{show_name} - "
        if not podcast_source.startswith(prefix):
            continue
        episode_title = podcast_source[len(prefix):]
        episode_id = (
            PodcastEpisode.objects.using("podcasts")
            .filter(podcast__show_name=show_name, episode_title=episode_title)
            .values_list("id", flat=True)
            .first()
        )
        if episode_id is None:
            continue
        podcaster = (
            SummaryRecord.objects.using("summariesdb")
            .filter(episode_id=episode_id)
            .exclude(podcaster="")
            .values_list("podcaster", flat=True)
            .first()
        )
        if podcaster:
            return podcaster

    return None


def _credibility_multiplier(podcaster: str) -> float:
    """
    講者歷史準確率 → 這條證據的權重倍數。查不到、或可評估樣本數不足
    （冷啟動，新講者還沒累積足夠的 pass/fail 紀錄）一律回傳 1.0（中性）。
    """
    row = (
        SpeakerAccuracy.objects.using("summariesdb")
        .filter(podcaster=podcaster, sector="")
        .first()
    )
    if not row or row.accuracy is None or row.evaluatable < CREDIBILITY_MIN_SAMPLE:
        return 1.0

    multiplier = 1.0 + (row.accuracy - CREDIBILITY_BASELINE) * CREDIBILITY_SENSITIVITY
    lo, hi = CREDIBILITY_CLAMP
    return max(lo, min(hi, multiplier))


# ──────────────────────────────────────────────
# B4：LLM 逐條判斷證據與驗證句的關係
# ──────────────────────────────────────────────

_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "stance": {"type": "string", "enum": list(STANCE_SIGN)},
                    "strength": {"type": "string", "enum": list(STRENGTH_WEIGHT)},
                    "note": {"type": "string"},
                },
                "required": ["index", "stance", "strength"],
            },
        },
    },
    "required": ["judgments"],
}

_JUDGE_PROMPT = """你是投資研究員。下面是一句財經 Podcast 裡的投資看法（驗證句），以及「同一時間前後、其他節目」
提到跟這個標的有關的產業關係證據。請逐條判斷：這條證據讓「這句驗證句」成立的可能性變高還是變低。

【驗證句】
標的：{asset}
方向：{direction}
內容：{thesis}
日期：{start_time}

【判斷標準】
- support：證據內容直接強化這句話的論點（例如驗證句看多 CoWoS 訂單，證據是輝達擴大向台積電追加 CoWoS 產能）
- contradict：證據內容削弱或反駁這句話的論點（例如驗證句看多，證據是主要客戶轉單給競爭對手）
- unrelated：證據跟這句話講的是不同面向（例如驗證句講 2 奈米製程，證據講的是海外設廠補助），
  或只是一般性的產業關係、看不出對這句話的影響
- strength：影響直接且明確填 strong；需要推論、影響間接填 weak
- 判斷對象永遠是「{asset} 這句驗證句」：證據就算對另一家公司有利，也要想清楚對 {asset} 是好是壞
  （例如驗證句看多 {asset}，證據是競爭對手搶到訂單 → 對 {asset} 是 contradict，不是 support）
- 只根據證據內容判斷，不要加入你自己對市場的看法
- note：一句話說明判斷理由（20 字內）

【證據】
{evidence}
"""

_RELATION_LABEL = {"Supply": "供應", "Co-impact": "共同受影響", "Substitution": "競爭替代"}


def _describe_cluster(asset: str, neighbor: str, relation_type: str, cluster_links: list[dict]) -> str:
    """把一個事實聚類寫成給 LLM 看的一行描述，Supply 要寫出誰供應誰。"""
    if relation_type == "Supply":
        asset_is_source = cluster_links[0].get("asset_is_source", True)
        pair = f"{asset} → {neighbor}" if asset_is_source else f"{neighbor} → {asset}"
        head = f"{pair}（{_RELATION_LABEL[relation_type]}，箭頭左邊是供應方）"
    else:
        head = f"{asset} ↔ {neighbor}（{_RELATION_LABEL.get(relation_type, relation_type)}）"
    reasons = "；".join(dict.fromkeys(l["reason"] for l in cluster_links if l.get("reason")))
    return f"{head}：{reasons}"


def _judge_evidence(asset: str, thesis: str, direction: Optional[str], start_time: date,
                    descriptions: list[str]) -> Optional[dict[int, dict]]:
    """
    一次判斷所有證據，回傳 {index: {"stance", "strength", "note"}}。
    沒有驗證句內容、或 LLM 呼叫／解析失敗時回傳 None，呼叫端退回 B1 / legacy。
    """
    if not thesis or not descriptions:
        return None
    direction_text = {"bullish": "看多", "bearish": "看空"}.get(direction or "", "未標示")
    evidence = "\n".join(f"{i}. {d}" for i, d in enumerate(descriptions))
    prompt = _JUDGE_PROMPT.format(
        asset=asset, direction=direction_text, thesis=thesis,
        start_time=start_time.isoformat(), evidence=evidence,
    )
    try:
        raw = _call_gemini_with_retry(
            prompt, response_schema=_JUDGE_SCHEMA, model=THESIS_JUDGE_MODEL, max_output_tokens=4096,
        )
        items = json.loads(raw).get("judgments", [])
    except Exception as e:
        print(f"  ⚠️ 證據判斷失敗，退回方向推估：{e}")
        return None
    judged = {
        it["index"]: it for it in items
        if isinstance(it.get("index"), int) and it.get("stance") in STANCE_SIGN
    }
    # 少判了幾條也不要整句作廢：沒判到的那幾條由呼叫端個別退回 B1 / legacy
    return judged or None


# ──────────────────────────────────────────────
# B5：說話者本身的歷史準確率（point-in-time）
# ──────────────────────────────────────────────

class SpeakerHistory:
    """
    一次載入所有已結算（pass / fail）的回測紀錄，供批次內每筆查詢
    「某講者在某日期之前」的準確率，不用每筆都打資料庫。

    跟 refresh_speaker_accuracy 一樣，同一講者的同一句 thesis 只算一次。
    跟 SpeakerAccuracy 表不同的是這裡只算 end_time < as_of 的紀錄——
    SpeakerAccuracy 是「現在」的快照，包含這句話之後才結算的結果，
    拿來評估過去的驗證句會用到未來資訊，回測時分數會好看得不真實。
    """

    def __init__(self):
        with connections["summariesdb"].cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ON (sr.podcaster, b.thesis)
                    sr.podcaster, b.end_time, b.result, tm.sector
                FROM backtesting b
                JOIN summaries_summaryrecord sr ON b.summary_id = sr.id
                LEFT JOIN (
                    SELECT ticker, MAX(sector) AS sector FROM ticker_map GROUP BY ticker
                ) tm ON tm.ticker = b.ticker
                WHERE b.result IN ('pass', 'fail')
                  AND sr.podcaster != '' AND b.thesis != ''
                  AND b.end_time IS NOT NULL
                ORDER BY sr.podcaster, b.thesis, b.id
                """
            )
            rows = cursor.fetchall()
            cursor.execute("SELECT ticker, MAX(sector) FROM ticker_map GROUP BY ticker")
            self.ticker_sector = {t: sec for t, sec in cursor.fetchall() if sec}

        # {podcaster: [(end_time, passed, sector)]}
        self.records: dict[str, list[tuple]] = defaultdict(list)
        for podcaster, end_time, result, sector in rows:
            self.records[podcaster].append((end_time, result == "pass", sector or ""))
        # 全體紀錄依結算日排序，供查詢「某日期之前的全體平均準確率」
        self._all = sorted((end_time, result == "pass") for _, end_time, result, _ in rows)

    def baseline(self, as_of: date) -> float:
        """as_of 之前已結算的全體講者平均準確率；還沒有任何紀錄時用 CREDIBILITY_BASELINE。"""
        settled = [passed for end_time, passed in self._all if end_time < as_of]
        return sum(settled) / len(settled) if settled else CREDIBILITY_BASELINE

    def adjustment(self, podcaster: Optional[str], ticker: Optional[str], as_of: date) -> dict:
        """
        回傳 {"podcaster", "scope", "sample", "accuracy", "adjustment"}。
        同產業樣本足夠時用同產業準確率（講者可能只擅長某些產業），不夠就用全體。
        """
        info = {"podcaster": podcaster, "scope": None, "sample": 0, "accuracy": None,
                "baseline": None, "adjustment": 0.0}
        history = [r for r in self.records.get(podcaster or "", []) if r[0] < as_of]
        sector = self.ticker_sector.get(ticker or "")
        same_sector = [r for r in history if sector and r[2] == sector]

        if len(same_sector) >= SPEAKER_MIN_SAMPLE:
            sample, scope = same_sector, sector
        elif len(history) >= SPEAKER_MIN_SAMPLE:
            sample, scope = history, "全體"
        else:
            info["sample"] = len(history)
            return info

        n = len(sample)
        accuracy = sum(passed for _, passed, _ in sample) / n
        baseline = self.baseline(as_of)
        gap = (accuracy - baseline) / SPEAKER_FULL_SCALE_GAP * n / (n + SPEAKER_SHRINK_K)
        gap = max(-1.0, min(1.0, gap))
        info.update(scope=scope, sample=n, accuracy=round(accuracy, 3), baseline=round(baseline, 3),
                    adjustment=round(SPEAKER_WEIGHT * gap, 1))
        return info


def compute_kg_score(
    asset: str,
    start_time: date,
    direction: Optional[str] = None,
    exclude_podcast_source=None,
    alias_index: Optional[AssetAliasIndex] = None,
    thesis: str = "",
    podcaster: Optional[str] = None,
    ticker: Optional[str] = None,
    speaker_history: Optional["SpeakerHistory"] = None,
) -> dict:
    """
    計算單一投資句的知識圖譜信心分數。

    direction：BacktestingRecord.direction（"bullish" / "bearish"）。
        有給、且查得到鄰居自己的方向時，才會套用 B1 的方向感知邏輯；
        沒給、或查不到鄰居方向時，退回舊版固定符號權重（LEGACY_SIGNED_WEIGHTS），
        行為等同於加入 B1/B3 之前的版本。

    alias_index：批次呼叫時建議傳入 _build_asset_alias_index()
        的結果（整批只建一次），用來把 asset 解析成知識圖譜裡可能的不同
        寫法（見 _resolve_asset_aliases()）。不給的話這裡會現算一次，只
        適合單筆呼叫（例如 debug_sample），批次跑會重複掃全部 node 很慢。

    thesis：驗證句內容，給了才會啟用 B4（LLM 逐條判斷證據與這句話的關係）。

    podcaster / ticker / speaker_history：啟用 B5（說話者準確率加減分）。
        批次呼叫時 speaker_history 請傳整批共用的 SpeakerHistory()；不給就不加減分。

    回傳：
        {
            "score": float,        # 0~100
            "raw": float,          # sigmoid 前的加權總和
            "link_count": int,     # 窗口內納入計分的原始 link 數
            "fact_count": int,     # 去重後的獨立事實數
            "window_days": int,    # 實際採用的時間窗口天數
            "evidence_raw": float, # 證據部分的加權總和（不含說話者加減分）
            "speaker": {...},      # B5 說話者準確率與加減分
            "facts": [...],        # 每個獨立事實的明細，供除錯/前端顯示佐證
        }
    """
    if alias_index is None:
        alias_index = _build_asset_alias_index()
    asset_aliases = _resolve_asset_aliases(asset, alias_index)

    # 先查 WINDOW_DAYS；完全沒有可用連線（已排除同一集自己的連線）時才放寬到
    # FALLBACK_WINDOW_DAYS。冷門股常常前 7 天剛好沒有別集提到，不代表沒有相關討論，
    # 直接給中性 50 分會低估；但有資料時仍以較新的 7 天為準，不混入較舊的證據。
    window_days = WINDOW_DAYS
    links = fetch_related_links(
        asset, start_time, exclude_podcast_source,
        window_days=window_days, asset_aliases=asset_aliases,
    )
    if not links:
        window_days = FALLBACK_WINDOW_DAYS
        links = fetch_related_links(
            asset, start_time, exclude_podcast_source,
            window_days=window_days, asset_aliases=asset_aliases,
        )

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for link in links:
        groups[(link["neighbor"], link["relation_type"])].append(link)

    thesis_dir = DIRECTION_SIGN.get(direction)
    window_start = start_time - timedelta(days=window_days)
    show_names = _all_show_names()

    # 先把所有事實聚類整理好，B4 才能一次把全部證據交給 LLM 判斷
    clustered = []
    for (neighbor, relation_type), group_links in groups.items():
        neighbor_dir = _neighbor_direction(neighbor, window_start, start_time)
        for cluster_links in _cluster_reasons(group_links):
            clustered.append((neighbor, relation_type, neighbor_dir, cluster_links))

    judgments = _judge_evidence(
        asset, thesis, direction, start_time,
        [_describe_cluster(asset, nb, rt, cl) for nb, rt, _, cl in clustered],
    ) or {}

    raw = 0.0
    facts = []
    for idx, (neighbor, relation_type, neighbor_dir, cluster_links) in enumerate(clustered):
        size = len(cluster_links)
        magnitude = MAGNITUDES.get(relation_type, 0)
        polarity = RELATION_POLARITY.get(relation_type, 0)
        judgment = judgments.get(idx)

        # ── B4：驗證句內容判斷（優先） ──
        if judgment is not None:
            strength = STRENGTH_WEIGHT.get(judgment.get("strength"), STRENGTH_WEIGHT["weak"])
            signed_weight = magnitude * STANCE_SIGN[judgment["stance"]] * strength
            direction_mode = "thesis"
        # ── B1：方向感知 ──
        elif neighbor_dir is not None and thesis_dir is not None:
            alignment = polarity * neighbor_dir * thesis_dir  # 大致落在 [-1, 1]
            signed_weight = magnitude * alignment
            direction_mode = "aligned"
        else:
            signed_weight = LEGACY_SIGNED_WEIGHTS.get(relation_type, 0)
            direction_mode = "legacy"

        # ── B3：來源可信度（同一聚類可能有多個講者，取平均） ──
        podcasters = {
            p for p in (
                _resolve_podcaster(l["podcast_source"], show_names) for l in cluster_links
            )
            if p
        }
        credibility = (
            sum(_credibility_multiplier(p) for p in podcasters) / len(podcasters)
            if podcasters else 1.0
        )

        contribution = signed_weight * (1 + math.log(size)) * credibility
        raw += contribution
        facts.append({
            "neighbor": neighbor,
            "relation_type": relation_type,
            "reason": cluster_links[0].get("reason", ""),
            "repeat_count": size,
            "neighbor_direction": neighbor_dir,
            "direction_mode": direction_mode,
            "stance": judgment.get("stance") if judgment else None,
            "strength": judgment.get("strength") if judgment else None,
            "judge_note": judgment.get("note", "") if judgment else "",
            "credibility": round(credibility, 2),
            "contribution": round(contribution, 1),
        })

    evidence_raw = raw

    # ── B5：說話者本身的歷史準確率 ──
    speaker = (
        speaker_history.adjustment(podcaster, ticker, start_time)
        if speaker_history is not None
        else {"podcaster": podcaster, "scope": None, "sample": 0, "accuracy": None,
              "baseline": None, "adjustment": 0.0}
    )
    raw += speaker["adjustment"]

    score = _sigmoid_score(raw)

    return {
        "score": round(score, 1),
        "raw": round(raw, 1),
        "evidence_raw": round(evidence_raw, 1),
        "speaker": speaker,
        "link_count": len(links),
        "fact_count": len(facts),
        "window_days": window_days,  # 實際採用的時間窗口（7 或放寬後的 14）
        "facts": facts,
    }


# ──────────────────────────────────────────────
# 寫入 thesis_score（Supabase，summariesdb，raw SQL，非 Django model）
# ──────────────────────────────────────────────

_DB_ALIASES_USED = ("summariesdb", "knowledge_graphdb", "podcasts")

# 資料庫連線層級的錯誤（斷網、DNS 解析失敗、連線被伺服器端關閉）是暫時性的，
# 網路/連線恢復就會成功，跟「這筆資料本身有問題」性質不同，不該當一般失敗
# 處理。實際發生過一次筆電網路斷線，導致後面幾千筆在幾秒內連續失敗（每筆都
# 立刻抛出同一種連線錯誤，沒有等待重試），這些紀錄要等下一次重新執行整個
# 指令才會補回來。改成跟 generate.py 429 配額重試同樣的精神：在同一筆上
# 用退避等待重試一段時間（最長約 25 分鐘），網路真的恢復就地繼續，不用
# 整個指令重跑；也避免短暫斷線時一次燒光大量紀錄的失敗次數。
_CONNECTION_MAX_RETRIES = 10
_CONNECTION_WAIT_CAP = 300  # 秒，單次最長等 5 分鐘


def _ensure_fresh_connections() -> None:
    """
    這個模組是被長時間跑的批次腳本呼叫（不是走 Django 的 request/response
    週期）。Django 預設「連線斷了自動偵測並重連」是掛在 request_started
    訊號上，腳本模式完全不會觸發——一旦連線被資料庫端關掉（閒置逾時、
    網路短暫斷線），後面每一筆都會沿用同一條已死的連線，整批壞光。
    在每筆紀錄開始前主動檢查一次，斷了就關掉，讓它在下次查詢時自動重開。
    """
    for alias in _DB_ALIASES_USED:
        connections[alias].close_if_unusable_or_obsolete()


def save_thesis_score(backtesting_id: int, result: dict) -> None:
    """
    把 compute_kg_score() 的回傳結果寫進 thesis_score 表。

    thesis_score 沒有 backtesting_id 的 UNIQUE 限制，單純 INSERT 在
    --recompute（skip_existing=False）或任何重算情境下會疊出重複列，
    不是取代舊分數。這裡先刪除這個 backtesting_id 的舊列再插入新的，
    讓「重算」在應用層有等同 upsert 的效果。
    """
    with connections["summariesdb"].cursor() as cursor:
        cursor.execute("DELETE FROM thesis_score WHERE backtesting_id = %s", [backtesting_id])
        cursor.execute(
            """
            INSERT INTO thesis_score (backtesting_id, score, raw, facts)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            [backtesting_id, result["score"], result["raw"], json.dumps(result["facts"])],
        )


def _already_scored_ids() -> set[int]:
    """已經寫過 thesis_score 的 backtesting_id 集合，跑批次時用來跳過重複計算。"""
    with connections["summariesdb"].cursor() as cursor:
        cursor.execute("SELECT DISTINCT backtesting_id FROM thesis_score")
        return {row[0] for row in cursor.fetchall()}


def _is_already_scored(backtesting_id: int) -> bool:
    """
    即時查一次這筆是否已經有分數。_already_scored_ids() 是整批開始前的
    快照，如果同時有兩個 process_backtesting_records() 在跑（例如手動重跑
    時忘記前一個還沒結束），兩邊都會用同一份舊快照、都判斷「還沒算過」而
    重複寫入——thesis_score 目前沒有 DB 層級的 UNIQUE 限制擋不住這種情況。
    這裡在真正寫入前再查一次資料庫當下的狀態，把重複窗口從「整批執行期間」
    縮小到「單筆處理期間」，成本很低（比起算分本身的 LLM 呼叫可忽略）。
    """
    with connections["summariesdb"].cursor() as cursor:
        cursor.execute("SELECT 1 FROM thesis_score WHERE backtesting_id = %s LIMIT 1", [backtesting_id])
        return cursor.fetchone() is not None


def _kg_progress_frontier() -> Optional[date]:
    """
    知識圖譜生成（generate_by_podcast.py 的 process_all_episodes）是照
    episode 的 published_at 由舊到新處理的，很可能還沒跑完。如果拿還沒被
    知識圖譜涵蓋到的日期去算 thesis_score，compute_kg_score 撈不到證據
    不是因為真的沒有相關討論，只是知識圖譜還沒處理到那個日期——分數會被
    誤判成「查無資料 → 中性 50 分」，而且知識圖譜之後補上那段資料也不會
    自動回頭重算。

    用 links 表目前最新的 summary_date 當作「知識圖譜已經處理到哪裡」的
    保守估計：因為是照處理順序寫入的，這個 MAX 值只會單調往前推進，用它
    當門檻不會漏算已經有資料的紀錄，頂多因為某些集數沒產生 link 而稍微
    保守一點。回傳 None 代表 links 表是空的（例如全新環境還沒跑過知識圖譜），
    這種情況呼叫端應該視為「什麼都還沒 ready」。
    """
    with connections["knowledge_graphdb"].cursor() as cursor:
        cursor.execute("SELECT MAX(summary_date) FROM links")
        row = cursor.fetchone()
    return row[0] if row else None


def _own_podcast_sources(rec) -> list[str]:
    """
    這句驗證句自己那一集，在 links.podcast_source 裡可能出現的寫法。
    舊管線（generate.py）存 SummaryRecord.source_filename；新管線
    （generate_by_podcast.py）存 f"{show_name} - {episode_title}"。
    原本只排除前者，新管線建出來的連線會讓驗證句被自己那一集的內容佐證。
    """
    sources = []
    if rec.summary_id and rec.summary:
        sources.append(rec.summary.source_filename or f"record-{rec.summary_id}")
    if rec.episode_id:
        ep = (
            PodcastEpisode.objects.using("podcasts")
            .select_related("podcast")
            .filter(pk=rec.episode_id)
            .first()
        )
        if ep:
            sources.append(f"{ep.podcast.show_name} - {ep.episode_title}")
    return sources


def process_backtesting_records(limit: Optional[int] = None, skip_existing: bool = True) -> dict:
    """
    批次跑 compute_kg_score() 並寫入 thesis_score。

    只處理 start_time 落在知識圖譜目前已涵蓋範圍內的紀錄（見
    _kg_progress_frontier()）；超出範圍的先跳過，不算失敗也不算已完成，
    留給知識圖譜跑到那個日期之後的下一次執行自然接續處理。

    skip_existing=True（預設）：已經有分數的 backtesting_id 直接跳過，
    重跑時不會產生重複列；除了整批開始前的快照判斷，每筆真正寫入前還會
    用 _is_already_scored() 再查一次即時狀態（見該函式說明，防止兩個
    程序同時跑造成重複）。
    """
    from apps.summaries.models import BacktestingRecord

    _ensure_fresh_connections()
    frontier = _kg_progress_frontier()

    records_qs = (
        BacktestingRecord.objects
        .using("summariesdb")
        .exclude(asset="")
        .exclude(start_time__isnull=True)
        .select_related("summary")
        .order_by("start_time")
    )

    if frontier is None:
        print("⚠️  知識圖譜（links 表）目前沒有任何資料，暫時沒有任何紀錄可以算分。")
        return {"processed": 0, "skipped": 0, "not_ready": 0, "failed": 0, "total": 0, "kg_frontier": None}

    eligible_qs = records_qs.filter(start_time__lte=frontier)
    not_ready = records_qs.filter(start_time__gt=frontier).count()

    if limit:
        eligible_qs = eligible_qs[:limit]
    records = list(eligible_qs)

    already_scored = _already_scored_ids() if skip_existing else set()
    # 整批只建一次（掃全部 node、逐一解析 ticker，node 數量一多會有點久），
    # 讓 compute_kg_score() 能把 asset 對應到知識圖譜裡可能的不同寫法。
    alias_index = _build_asset_alias_index()
    speaker_history = SpeakerHistory()

    processed = 0
    skipped = 0
    failed = 0

    for i, rec in enumerate(records, 1):
        connection_attempt = 0
        while True:
            try:
                # 每筆最前面就先確認連線還活著，包含底下的 _is_already_scored()
                # 也要用到連線——之前這個檢查寫在 try 區塊外面、又沒先確認連線，
                # 筆電睡眠恢復後第一筆處理時連線已經失效，直接讓整個程式當掉，
                # 現在整筆（含跳過判斷）都在同一個 try 裡，連線壞掉不會讓整個
                # 批次程序死掉。
                _ensure_fresh_connections()

                if skip_existing and (rec.id in already_scored or _is_already_scored(rec.id)):
                    skipped += 1
                    break

                print(f"[{i}/{len(records)}] #{rec.id} [{rec.asset}] {rec.direction} @ {rec.start_time}")
                result = compute_kg_score(
                    rec.asset, rec.start_time,
                    direction=rec.direction,
                    exclude_podcast_source=_own_podcast_sources(rec),
                    alias_index=alias_index,
                    thesis=rec.thesis,
                    podcaster=rec.summary.podcaster if rec.summary_id and rec.summary else None,
                    ticker=rec.ticker,
                    speaker_history=speaker_history,
                )
                save_thesis_score(rec.id, result)
                print(
                    f"  -> score={result['score']}  fact_count={result['fact_count']}  "
                    f"window={result['window_days']}d  speaker_adj={result['speaker']['adjustment']}"
                )
                processed += 1
                break

            except (OperationalError, InterfaceError) as e:
                # 連線層級的錯誤（斷網、DNS 解析失敗）是暫時性的，在同一筆
                # 上重試撐一段時間，不要立刻放棄、也不要往下一筆繼續衝——
                # 實際發生過一次斷網，讓後面幾千筆在幾秒內連續失敗，因為
                # 每筆都立刻撞到同一個還沒恢復的連線。
                connection_attempt += 1
                for alias in _DB_ALIASES_USED:
                    try:
                        connections[alias].close()
                    except Exception:
                        pass
                if connection_attempt >= _CONNECTION_MAX_RETRIES:
                    print(f"  ❌ #{rec.id} 資料庫連線重試 {_CONNECTION_MAX_RETRIES} 次仍失敗，跳過此筆。錯誤：{e}")
                    failed += 1
                    break
                wait = min(30 * connection_attempt, _CONNECTION_WAIT_CAP)
                print(f"  ⏸️  #{rec.id} 資料庫連線異常（第 {connection_attempt}/{_CONNECTION_MAX_RETRIES} 次，可能是網路問題），{wait}s 後重試...錯誤：{e}")
                time.sleep(wait)

            except Exception as e:
                print(f"  ❌ #{rec.id} 失敗，跳過此筆。錯誤：{e}")
                failed += 1
                break

    summary = {
        "processed": processed,
        "skipped": skipped,
        "not_ready": not_ready,
        "failed": failed,
        "total": len(records),
        "kg_frontier": frontier.isoformat(),
    }
    print(
        f"\n完成！寫入 {processed} 筆、跳過已存在 {skipped} 筆、失敗 {failed} 筆"
        f"（共處理 {len(records)} 筆；知識圖譜目前處理到 {frontier}，"
        f"另有 {not_ready} 筆超出範圍暫不處理，留給之後的執行接續）。"
    )
    return summary


# ──────────────────────────────────────────────
# 測試用：從 BacktestingRecord 挑幾筆真實投資句實際跑一次
# ──────────────────────────────────────────────

def debug_sample(limit: int = 5):
    """
    Django shell 測試用：
        from apps.knowledge_graph.services.thesis_score import debug_sample
        debug_sample()
    """
    from apps.summaries.models import BacktestingRecord

    records = (
        BacktestingRecord.objects
        .using("summariesdb")
        .exclude(asset="")
        .exclude(start_time__isnull=True)
        .select_related("summary")
        .order_by("-start_time")[:limit]
    )

    speaker_history = SpeakerHistory()
    for rec in records:
        result = compute_kg_score(
            rec.asset, rec.start_time,
            direction=rec.direction,
            exclude_podcast_source=_own_podcast_sources(rec),
            thesis=rec.thesis,
            podcaster=rec.summary.podcaster if rec.summary_id and rec.summary else None,
            ticker=rec.ticker,
            speaker_history=speaker_history,
        )

        sp = result["speaker"]
        print(f"\n[{rec.asset}] {rec.direction} @ {rec.start_time}")
        print(f"  thesis: {rec.thesis[:50]}")
        print(f"  score={result['score']}  raw={result['raw']}（證據 {result['evidence_raw']}）  "
              f"link_count={result['link_count']}  fact_count={result['fact_count']}  "
              f"window={result['window_days']}d")
        print(f"  speaker={sp['podcaster']}  scope={sp['scope']}  sample={sp['sample']}  "
              f"accuracy={sp['accuracy']}  baseline={sp['baseline']}  adjustment={sp['adjustment']}")
        for f in result["facts"]:
            nd = "?" if f["neighbor_direction"] is None else f"{f['neighbor_direction']:+.2f}"
            print(f"    {f['neighbor']:15s} {f['relation_type']:12s} "
                  f"repeat={f['repeat_count']}  neighbor_dir={nd}  mode={f['direction_mode']:7s}  "
                  f"stance={f['stance']}/{f['strength']}  "
                  f"credibility={f['credibility']}  contribution={f['contribution']}  {f['judge_note']}")
