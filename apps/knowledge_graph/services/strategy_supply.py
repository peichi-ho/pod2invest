# apps/knowledge_graph/services/strategy_supply.py
"""
Supply 策略：找 seed 的上遊供應商與下遊客戶

資料庫的 Supply 邊語意是「source 供應 target」（見 generate.py 的抽取 prompt）。

- upstream（誰供應 seed，供應鏈受惠股）：
    PPR 需要沿邊的反方向走（target → source），
    做法是把 (source, target) 對調後再建圖，讓 seed 的 out-edge 指向它的供應商。
- downstream（seed 供應給誰）：
    PPR 沿邊的原方向走（source → target）。

兩份候選清單分開回傳，各自以 PPR 分數排序。
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

from .graph_window import build_weighted_graph, fetch_links_in_window
from .ppr_utils import run_ppr

_MIN_MAJORITY_RATIO = 0.70
_MIN_WILSON_LOWER_BOUND = 0.60
_WILSON_Z = 1.96  # 95% 信賴水準


def _wilson_lower_bound(successes: int, total: int, z: float = _WILSON_Z) -> float:
    """Wilson score interval 的下界。樣本數小時會比原始比例保守很多，
    避免「2集比1集」這種小樣本就直接判定方向。"""
    if total == 0:
        return 0.0
    p_hat = successes / total
    denom = 1 + z**2 / total
    center = p_hat + z**2 / (2 * total)
    margin = z * math.sqrt(p_hat * (1 - p_hat) / total + z**2 / (4 * total**2))
    return (center - margin) / denom


def _clean_conflicting_edges(links: list[dict]) -> list[dict]:
    """
    PPR 建圖前，先處理雙向都存在的衝突 pair，避免方向記反的高提及量 pair
    （例如台積電↔輝達）在 PPR 計算階段主宰分數分佈，把真正的供應商擠到候選
    名單很後面。純統計判斷，不呼叫 LLM；候選名單出來後，Selection 階段還有
    一層 LLM 方向判斷做最後把關，所以這裡不需要追求完美判準，只要擋掉最
    誇張的雜訊即可。

    做法（淨方向差額）：多數方向保留 (多數次數 - 少數次數) 筆，少數方向全部
    捨棄。差距懸殊時淨值接近原始多數次數，幾乎不損失訊號；差距不大但仍有
    偏向時，淨值縮小但不會像整組丟棄那樣把所有證據都浪費掉；兩方向剛好打
    平時淨值為 0，效果等同於丟棄，但是自然算出來的結果，不需要武斷門檻。

    比較嚴格的替代方案見 _clean_conflicting_edges_wilson()：實測對長尾、
    提及量少的標的（如辛耘、萬潤）會過度剔除，跟這個系統「找出原本沒注意
    到的標的」的目的衝突，所以目前不採用，只保留在這裡供之後參考/實驗。
    """
    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for lk in links:
        by_pair[(lk["source"], lk["target"])].append(lk)

    seen_pairs: set[tuple[str, str]] = set()
    kept: list[dict] = []

    for (s, t), fwd_links in by_pair.items():
        key = tuple(sorted([s, t]))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        rev_links = by_pair.get((t, s), [])
        if not rev_links:
            kept.extend(fwd_links)  # 沒有反向邊，不衝突
            continue

        if len(fwd_links) >= len(rev_links):
            majority, net = fwd_links, len(fwd_links) - len(rev_links)
        else:
            majority, net = rev_links, len(rev_links) - len(fwd_links)

        kept.extend(majority[:net])

    return kept


def _clean_conflicting_edges_wilson(links: list[dict]) -> list[dict]:
    """
    備用方案（目前未使用）：以「不同集數」（podcast_source）為投票單位，而非
    原始筆數，避免單一集重複提及同一件事就灌票。雙向都存在的 pair 需同時
    通過兩項統計檢定才保留多數方向：
      1. 多數方向集數佔比 >= 70%
      2. Wilson 95% 信賴區間下界 >= 60%
    兩項都通過，保留多數方向的全部原始邊；任一項不通過，兩個方向都丟棄。

    統計上比 _clean_conflicting_edges() 嚴謹很多，但實測對提及量少的長尾
    標的過度剔除（因為 Wilson 區間在小樣本下很保守），跟這個系統要找出
    「原本沒注意到的標的」的目的衝突，所以改用淨差額版本。留著供之後想
    重新比較，或想針對特定情境（例如提及量夠大時）切換使用。
    """
    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for lk in links:
        by_pair[(lk["source"], lk["target"])].append(lk)

    seen_pairs: set[tuple[str, str]] = set()
    kept: list[dict] = []

    for (s, t), fwd_links in by_pair.items():
        key = tuple(sorted([s, t]))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        rev_links = by_pair.get((t, s), [])
        if not rev_links:
            kept.extend(fwd_links)  # 沒有反向邊，不衝突
            continue

        n_fwd = len({lk["podcast_source"] for lk in fwd_links if lk["podcast_source"]})
        n_rev = len({lk["podcast_source"] for lk in rev_links if lk["podcast_source"]})
        total = n_fwd + n_rev
        if total == 0:
            continue  # 沒有集數資訊可判斷方向，保守丟棄

        if n_fwd >= n_rev:
            majority_links, majority_n = fwd_links, n_fwd
        else:
            majority_links, majority_n = rev_links, n_rev

        ratio = majority_n / total
        if ratio >= _MIN_MAJORITY_RATIO and _wilson_lower_bound(majority_n, total) >= _MIN_WILSON_LOWER_BOUND:
            kept.extend(majority_links)
        # 任一項未通過 → 兩個方向都不納入建圖

    return kept


def supply_candidates(
    seed: str,
    end_date: date,
    window_days: int,
    damping: float = 0.85,
) -> dict:
    links = fetch_links_in_window(end_date, window_days, relation_type="Supply")
    if not links:
        return {"upstream": [], "downstream": []}

    links = _clean_conflicting_edges(links)
    if not links:
        return {"upstream": [], "downstream": []}

    # downstream：seed 供應給誰 → 沿原方向 (source -> target)
    G_down, meta_down = build_weighted_graph(links, directed=True)
    downstream = run_ppr(G_down, seed, edge_meta=meta_down, damping=damping)

    # upstream：誰供應 seed → 對調 source/target 後建圖，等同沿原邊反方向走
    reversed_links = [{**lk, "source": lk["target"], "target": lk["source"]} for lk in links]
    G_up, meta_up = build_weighted_graph(reversed_links, directed=True)
    upstream = run_ppr(G_up, seed, edge_meta=meta_up, damping=damping)

    return {"upstream": upstream, "downstream": downstream}
