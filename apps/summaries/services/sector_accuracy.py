"""
Rankings 產業篩選用：「標的 → 8 大類」的判斷，以及分產業的講者準確率。

排名頁（AccuracyRankingAPIView）跟節目頁（BacktestingListAPIView 帶 sector）共用這一份，
兩邊的「N 筆」才會一致。

分類以 tag_ticker_sectors.TICKER_SECTOR 為準（該檔開頭註明「前端顯示用」），**不看**資料庫的
ticker_map.sector——那是另一套更細的分類（「半導體」「電子/硬體」…），拿 Rankings 的類別名稱去比
會永遠篩不到東西。也因此這裡不依賴 speaker_accuracy 快照的 sector 欄位：有人重跑
refresh_speaker_accuracy 不會再讓篩選失效。

不在對照表裡的標的（上櫃股、加密貨幣、櫃買指數…）一律歸「其他產業」。
"""
from django.core.cache import cache
from django.db import connections

from apps.summaries.management.commands.tag_ticker_sectors import TICKER_SECTOR

FALLBACK_SECTOR = "其他產業"

# 順序跟 templates/pages/rankings.html 的下拉選單一致
SECTORS = (
    "半導體/電子元件", "科技類", "台股指數", "海外指數",
    "大宗商品/外匯", "ETF", "能源/電動車", FALLBACK_SECTOR,
)

_CACHE_KEY = "sector-accuracy:v1"
_CACHE_SECONDS = 300   # 回測結果一天才更新幾次，排名頁不需要每次點篩選都重算


def sector_of(ticker) -> str:
    return TICKER_SECTOR.get((ticker or "").strip(), FALLBACK_SECTOR)


def ticker_sector_clause(sector: str, column: str = "b.ticker"):
    """
    回傳 (SQL 片段, 參數 list)，可直接接在 WHERE 後面；sector 不是 8 大類之一時回傳 None。

    「其他產業」包含不在對照表的標的，沒辦法列舉，所以用「不屬於其他 7 類」反向篩選；
    COALESCE 讓 ticker 為 NULL 的列也歸到這一類（跟 sector_of() 的行為一致）。
    """
    if sector not in SECTORS:
        return None
    if sector == FALLBACK_SECTOR:
        others = [t for t, s in TICKER_SECTOR.items() if s != FALLBACK_SECTOR]
        return f"AND NOT (COALESCE({column}, '') = ANY(%s))", [others]
    members = [t for t, s in TICKER_SECTOR.items() if s == sector]
    return f"AND {column} = ANY(%s)", [members]


# 去重規則跟 refresh_speaker_accuracy._SQL_OVERALL 完全一樣（只取 pro、同一筆預測只算一次），
# 差別只在這裡把 ticker 一起撈出來，歸類交給 sector_of()。
_SQL_DEDUPED_PREDICTIONS = """
    SELECT DISTINCT ON (sr.podcaster, b.episode_id, b.asset, b.ticker, b.direction,
                        b.timeframe_raw, b.start_time, b.end_time, b.thesis)
        sr.podcaster, b.ticker, b.result
    FROM backtesting b
    JOIN summaries_summaryrecord sr ON b.summary_id = sr.id
    WHERE b.result IN ('pass','fail')
      AND sr.mode = 'pro'
      AND sr.podcaster != ''
      AND b.thesis != ''
    ORDER BY sr.podcaster, b.episode_id, b.asset, b.ticker, b.direction,
             b.timeframe_raw, b.start_time, b.end_time, b.thesis, b.id
"""


def aggregate_by_sector(rows) -> dict:
    """rows: (podcaster, ticker, result) → {sector: [{podcaster, pass, fail, total, accuracy}, ...]}"""
    counts: dict[str, dict[str, list]] = {s: {} for s in SECTORS}
    for podcaster, ticker, result in rows:
        c = counts[sector_of(ticker)].setdefault(podcaster, [0, 0])   # [pass, fail]
        c[0 if result == "pass" else 1] += 1

    out = {}
    for sector, pods in counts.items():
        items = []
        for podcaster, (p, f) in pods.items():
            total = p + f
            items.append({
                "podcaster": podcaster, "pass": p, "fail": f, "total": total,
                "accuracy": round(p / total * 100), "_ratio": p / total,
            })
        # 準確率高的在前，同分時樣本多的在前（跟快照 API 的排序一致）
        items.sort(key=lambda x: (-x["_ratio"], -x["total"]))
        for it in items:
            del it["_ratio"]
        out[sector] = items
    return out


def accuracy_by_sector() -> dict:
    """8 大類各自的講者準確率（一次算完全部類別，快取 5 分鐘）。"""
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached
    with connections["summariesdb"].cursor() as c:
        c.execute(_SQL_DEDUPED_PREDICTIONS)
        rows = c.fetchall()
    result = aggregate_by_sector(rows)
    cache.set(_CACHE_KEY, result, _CACHE_SECONDS)
    return result
