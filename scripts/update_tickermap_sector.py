# scripts/update_tickermap_sector.py
"""
用 industry_classification.py 的18個大分類對照表(TWSE 34類/yfinance sector
彙整而成，2026-09-30)，重新計算並更新 TickerMap.sector——取代原本人工維護、
只有8種但太籠統(「科技類」「其他產業」佔了一半以上)的舊分類。

每一檔ticker先去 IndustryClassification 表查細分類，再用 to_broad_sector()
換算成大分類寫回 TickerMap.sector。IndustryClassification 裡沒有的ticker
(理論上不該發生，除非兩張表不同步)會被跳過並列在失敗清單。

用法：
  python scripts/update_tickermap_sector.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.summaries.models import TickerMap, IndustryClassification
from apps.summaries.services.industry_classification import to_broad_sector

classification_by_ticker = {
    o.ticker: o for o in IndustryClassification.objects.using("summariesdb").all()
}

updated, unchanged, missing = 0, 0, []
for tm in TickerMap.objects.using("summariesdb").all():
    classification = classification_by_ticker.get(tm.ticker)
    if not classification:
        missing.append(tm.ticker)
        continue
    broad = to_broad_sector(classification)
    if tm.sector != broad:
        old = tm.sector
        tm.sector = broad
        tm.save(using="summariesdb", update_fields=["sector"])
        updated += 1
        print(f"{tm.ticker} ({tm.asset_name}): {old!r} -> {broad!r}")
    else:
        unchanged += 1

print()
print(f"完成：更新 {updated} 筆，不變 {unchanged} 筆，缺對照資料 {len(missing)} 筆")
if missing:
    print("缺對照資料的ticker:", missing)
