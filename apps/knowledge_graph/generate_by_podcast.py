"""
apps/knowledge_graph/generate_by_podcast.py

以 Podcast 逐字稿原文（podcasts_transcript.srt_content）為資料來源，
取代 generate.py 原本讀 summariesdb 摘要欄位（one_sentence_summary /
investment_takeaways / arguments / entities.companies_or_stocks）的做法。
輸出結構（nodes / links、relation_type: Supply / Co-impact / Substitution）
與 generate.py 相同，寫入同一個 knowledge_graphdb。

抽取本身交給 services/extract_v2.py 的三步驟流程（公司抽取 → 候選組合 →
逐組判斷關係，引用句逐字驗證）。原本「一次丟 4 萬字給 LLM 同時找公司、
判斷關係」再加 LLM 二階審查的做法，在 20 集人工標註評估集上連線召回率
只有 22.6%、precision 24.6%，且將近一半的連線有一端是產業或產品；
三步驟流程兩者都提升到約 40%，非公司節點幾乎消失。

這份檔案負責把抽取結果寫進資料庫，寫入前仍保留幾道規則防線：

1. 名稱統一
   抽出的公司名稱先比對 summariesdb.ticker_map（asset_name / zh_name / ticker）
   別名索引，命中就用 ticker_map 的正式名稱；比對不到才退回 generate.py
   原本「既有節點正規化 → LLM 語意判斷」那一套，避免同一家公司重複建節點。

2. reason 規則式黑名單（_is_vague_reason）
   不完全依賴 LLM 照著 prompt 做，寫入前再擋一次籠統的 reason。

3. Supply 方向一致性
   Supply（誰供應誰）具有結構性方向，不應該同一組公司在相近時間內同時
   出現 A→B 又出現 B→A。寫入前會檢查資料庫中 7 天內是否已存在反方向的
   Supply 連線，有的話就跳過（視為需要人工複核的衝突），而不是照單全收。
   Substitution（互相搶市占）不做這個檢查——7 天內 A 搶下 B 的訂單、
   B 隔幾天反攻搶回來都是有可能真實發生的競爭態勢，不是資料錯誤。
"""
import re
from datetime import datetime, timedelta
from typing import Optional

from django.db import connections

from apps.knowledge_graph.generate import (
    _normalize_node_name,
    _resolve_node_name,
    _link_duplicate_exists,
)
from apps.knowledge_graph.services.extract_v2 import CompanyDirectory, extract_graph_v2
from apps.podcasts.models import PodcastEpisode
from apps.summaries.models import TickerMap


# ==========================================
# 模組 1：公司別名權威表（ticker_map）
# ==========================================

def build_ticker_alias_index() -> dict[str, str]:
    """
    從 summariesdb.ticker_map 建立 {正規化別名: 正式名稱（asset_name）}，
    asset_name / zh_name / ticker 三欄都當別名，供 O(1) 查詢抽取結果的名稱。
    """
    norm_index: dict[str, str] = {}
    records = TickerMap.objects.using("summariesdb").values("asset_name", "zh_name", "ticker")
    for r in records:
        canonical = (r["asset_name"] or "").strip()
        if not canonical:
            continue
        for alias in {r["asset_name"], r["zh_name"], r["ticker"]}:
            alias = (alias or "").strip()
            if len(alias) >= 2:
                norm_index[_normalize_node_name(alias)] = canonical
    return norm_index


def _resolve_company_canonical(
    name: str,
    ticker_norm_index: dict[str, str],
    existing_names: list[str],
    existing_norm_index: dict[str, str],
) -> str:
    """
    決定抽取出的公司名稱該對應到哪個正式節點名稱。

    優先序：
    1. ticker_map 別名表（權威、決定性，不呼叫 LLM）
    2. 既有 KG 節點（generate.py 的正規化 + LLM 語意比對）
    """
    key = _normalize_node_name(name)
    if key in ticker_norm_index:
        return ticker_norm_index[key]
    return _resolve_node_name(name, existing_names, existing_norm_index)


# ==========================================
# 模組 2：reason 品質規則式黑名單
# ==========================================

_VAGUE_REASON_PATTERNS = [
    r"都(是|屬於|來自)同?一?個?產業",
    r"同屬.{0,10}(產業|類股|概念|賽道)",
    r"(皆|都).{0,6}(podcast|節目|本集|主持人|來賓).{0,4}提及",
    r"(皆|都)受.{0,10}影響",
    r"(皆|都)受惠於.{0,10}(熱潮|趨勢|循環)$",
    r"(皆|都)是.{0,6}(科技|AI|半導體|電子).{0,4}公司",
    r"(皆|都)在.{0,6}(同一|相同).{0,4}(市場|領域)",
]
_VAGUE_REASON_RE = re.compile("|".join(_VAGUE_REASON_PATTERNS))


def _is_vague_reason(reason: str) -> bool:
    """規則式黑名單：擋掉 prompt 規則沒攔到的籠統 reason（不呼叫 LLM）。"""
    reason = (reason or "").strip()
    if len(reason) < 8:
        return True
    return bool(_VAGUE_REASON_RE.search(reason))


# ==========================================
# 模組 3：Supply 方向一致性
# ==========================================

# 具方向性、需要防止 A→B 與 B→A 同時存在的 relation_type。
# Co-impact（共同受惠/受害於同一件事）是對稱關係，不檢查方向。
# Substitution 不擋：7 天內兩邊互相搶對方市占是有可能真實發生的
# （例如 A 搶下 B 的訂單、B 隔幾天又反攻搶回來），不是資料錯誤，不應該互相排斥。
_DIRECTIONAL_RELATION_TYPES = {"Supply"}

# 只在summary_date 前後 N 天內查衝突，而不是不限期間 —— 供應鏈/競爭態勢
# 本身可能隨時間演變（例如轉單、市占易主），太舊的反方向紀錄不該一直卡住新資料。
DIRECTION_CONFLICT_WINDOW_DAYS = 7


def _direction_conflict(cursor, source: str, target: str, relation_type: str, summary_date: str) -> bool:
    """
    檢查 [summary_date - N天, summary_date + N天] 內，是否已存在反方向
    （target → source）、同 relation_type 的連線。目前只對 Supply 做此檢查。
    """
    if relation_type not in _DIRECTIONAL_RELATION_TYPES:
        return False

    center = datetime.fromisoformat(summary_date).date() if isinstance(summary_date, str) else summary_date
    window_start = center - timedelta(days=DIRECTION_CONFLICT_WINDOW_DAYS)
    window_end = center + timedelta(days=DIRECTION_CONFLICT_WINDOW_DAYS)

    cursor.execute(
        """
        SELECT 1 FROM links
        WHERE source = %s AND target = %s AND relation_type = %s
          AND summary_date BETWEEN %s AND %s
        LIMIT 1
        """,
        [target, source, relation_type, window_start, window_end],
    )
    return cursor.fetchone() is not None


# ==========================================
# 模組 4：寫入 Supabase (knowledge_graphdb)
# ==========================================

def save_to_db(
    extracted: dict,
    summary_date: str,
    podcast_source: str,
    ticker_norm_index: dict[str, str],
):
    """
    extracted：extract_graph_v2() 的回傳值。
    只為「有出現在連線裡」的公司建立節點，避免本集只是順帶提到的公司變成孤立節點。
    """
    links = extracted.get("links", [])
    industry_of = {e["name"]: e.get("industry") or "其他" for e in extracted.get("entities", [])}

    nodes_inserted = 0
    links_inserted = 0
    links_skipped_vague = 0
    links_skipped_direction = 0

    with connections["knowledge_graphdb"].cursor() as cursor:

        cursor.execute("SELECT name, industry FROM nodes")
        existing_nodes: dict = {row[0]: row[1] for row in cursor.fetchall()}
        existing_names: list = list(existing_nodes.keys())
        existing_norm_index: dict = {_normalize_node_name(n): n for n in existing_names}

        # ── 階段一：建立 抽取名稱 → 正式名稱 對照表，並插入真正新的 nodes ──
        name_map: dict = {}
        for name in dict.fromkeys(n for link in links for n in (link["source"], link["target"])):
            canonical = _resolve_company_canonical(
                name, ticker_norm_index, existing_names, existing_norm_index
            )
            name_map[name] = canonical

            if canonical not in existing_nodes:
                industry = industry_of.get(name, "其他")
                cursor.execute(
                    "INSERT INTO nodes (name, industry) VALUES (%s, %s)",
                    [canonical, industry],
                )
                existing_nodes[canonical] = industry
                existing_names.append(canonical)
                existing_norm_index[_normalize_node_name(canonical)] = canonical
                nodes_inserted += 1
                print(f"  ✅ 新增 node：{canonical} ({industry})")
            elif canonical != name:
                print(f"  ⏭️  node 合併至既有：{canonical}（原始：{name}）")

        # ── 階段二：替換 source/target 為正式名稱，過規則防線後插入 links ──
        for link in links:
            source = name_map[link["source"]]
            target = name_map[link["target"]]
            relation_type = link["relation_type"]
            reason = link.get("reason", "")

            if source == target:
                print(f"  ⏭️  跳過自環 link：{source} → {target}")
                continue

            if _is_vague_reason(reason):
                print(f"  ⏭️  reason 過於籠統，跳過：{source} → {target}（{reason}）")
                links_skipped_vague += 1
                continue

            if _direction_conflict(cursor, source, target, relation_type, summary_date):
                print(f"  ⏭️  {relation_type} 方向衝突（{DIRECTION_CONFLICT_WINDOW_DAYS} 天內已存在 {target} → {source}），跳過：{source} → {target}")
                links_skipped_direction += 1
                continue

            if _link_duplicate_exists(cursor, source, target, reason):
                print(f"  ⏭️  link 重複，跳過：{source} → {target}")
            else:
                cursor.execute(
                    """
                    INSERT INTO links (source, target, relation_type, reason, summary_date, podcast_source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [source, target, relation_type, reason, summary_date, podcast_source],
                )
                links_inserted += 1

    print(
        f"✅ [資料庫] 完成！新增 {nodes_inserted} 個節點，{links_inserted} 條連線"
        f"（跳過籠統 reason {links_skipped_vague} 條、方向衝突 {links_skipped_direction} 條）。"
    )


# ==========================================
# 模組 5：Pipeline
# ==========================================

def _podcast_source_for(episode: PodcastEpisode) -> str:
    return f"{episode.podcast.show_name} - {episode.episode_title}"


def process_one_episode(
    episode: PodcastEpisode,
    ticker_norm_index: dict[str, str],
    directory: Optional[CompanyDirectory] = None,
):
    if not hasattr(episode, "transcript") or not episode.transcript or not episode.transcript.srt_content:
        print(f"  ⚠️ 無逐字稿，跳過：{episode.episode_title}")
        return

    podcast_source = _podcast_source_for(episode)
    summary_date = (episode.published_at or episode.transcript.updated_at).date().isoformat()

    print("\n🧠 三步驟抽取（公司 → 候選組合 → 關係判斷）...")
    extracted = extract_graph_v2(episode.transcript.srt_content, directory=directory)
    stats = extracted.get("stats", {})
    if stats.get("empty_transcript"):
        print(f"  ⚠️ 逐字稿內容為空，跳過：{episode.episode_title}")
        return
    print(
        f"  公司 {len(extracted['entities'])} 家｜候選組合 {stats.get('candidate_pairs', 0)}"
        f"｜連線 {len(extracted['links'])} 條（引用句不符丟棄 {stats.get('dropped_bad_quote', 0)}）"
    )

    save_to_db(
        extracted,
        summary_date=summary_date,
        podcast_source=podcast_source,
        ticker_norm_index=ticker_norm_index,
    )


def process_all_episodes(
    limit: Optional[int] = None,
    published_after: Optional[str] = None,
    published_before: Optional[str] = None,
):
    """
    從 podcasts DB 讀取有逐字稿的 PodcastEpisode，依序萃取知識圖譜並寫入 knowledge_graphdb。

    published_after / published_before：依 PodcastEpisode.published_at 篩選區間
    （格式 "YYYY-MM-DD"，含頭尾兩端），用於先拿一小段時間的資料測試，不必一次跑全部。
    這個欄位跟寫入 links.summary_date 用的是同一個（見 process_one_episode），
    所以篩選範圍會跟實際存進去的 summary_date 一致，不會像用 transcript.updated_at
    篩選時出現「篩的是校稿時間、存的是節目發布時間」兩者對不上的落差。
    """
    episodes = (
        PodcastEpisode.objects.using("podcasts")
        .select_related("podcast", "transcript")
        .order_by("published_at")
    )
    if published_after:
        episodes = episodes.filter(published_at__date__gte=published_after)
    if published_before:
        episodes = episodes.filter(published_at__date__lte=published_before)
    if limit:
        episodes = episodes[:limit]

    total = episodes.count()
    print(f"\n📚 共 {total} 集待處理")

    with connections["knowledge_graphdb"].cursor() as cursor:
        cursor.execute("SELECT DISTINCT podcast_source FROM links")
        processed_sources = {row[0] for row in cursor.fetchall()}
    print(f"⏭️  已處理過：{len(processed_sources)} 筆")

    ticker_norm_index = build_ticker_alias_index()
    directory = CompanyDirectory()

    skipped = 0
    for i, episode in enumerate(episodes, 1):
        podcast_source = _podcast_source_for(episode)
        if podcast_source in processed_sources:
            skipped += 1
            continue

        # 這個迴圈可能因為 429 配額重試（見 generate.py _call_gemini_with_retry）
        # 而連續跑好幾個小時，DB 連線閒置太久會被伺服器端關掉；主動檢查一次，
        # 斷了就關掉讓它在下次查詢時自動重開，避免整批後面全部連線錯誤。
        for alias in ("podcasts", "summariesdb", "knowledge_graphdb"):
            connections[alias].close_if_unusable_or_obsolete()

        print(f"\n[{i}/{total}] 處理單集：{podcast_source}")
        try:
            process_one_episode(episode, ticker_norm_index, directory)
        except Exception as e:
            print(f"❌ [{i}/{total}] 處理失敗，跳過此筆。錯誤：{e}")

    print(f"\n✅ 完成！略過已處理 {skipped} 筆，共處理 {total - skipped} 筆。")


def process_new_episode(episode_id: int):
    """處理單一集（供手動觸發使用）。"""
    try:
        episode = (
            PodcastEpisode.objects.using("podcasts")
            .select_related("podcast", "transcript")
            .get(pk=episode_id)
        )
    except PodcastEpisode.DoesNotExist:
        print(f"❌ PodcastEpisode #{episode_id} 不存在")
        return

    print(f"\n🚀 處理單集：{_podcast_source_for(episode)}")
    process_one_episode(episode, build_ticker_alias_index())
