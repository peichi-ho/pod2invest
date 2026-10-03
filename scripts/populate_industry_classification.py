# scripts/populate_industry_classification.py
"""
用 TickerMap 裡現有的全部股票代號，逐一查詢產業分類(先試TWSE官方分類，查不到
才用yfinance)，寫入獨立的 IndustryClassification 表。

用法：
  python scripts/populate_industry_classification.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.summaries.models import TickerMap, IndustryClassification
from apps.summaries.services.industry_classification import classify_ticker

tickers = list(
    TickerMap.objects.using("summariesdb").values_list("ticker", flat=True).distinct()
)
print(f"TickerMap裡共 {len(tickers)} 個不重複的ticker")

created, updated, failed = 0, 0, []
for i, ticker in enumerate(tickers):
    try:
        result = classify_ticker(ticker)
    except Exception as e:
        failed.append((ticker, str(e)))
        continue

    obj, is_created = IndustryClassification.objects.using("summariesdb").update_or_create(
        ticker=ticker,
        defaults={
            "market": result["market"],
            "industry_code": result["industry_code"],
            "industry_name": result["industry_name"],
            "sector_name": result["sector_name"],
            "source": result["source"],
        },
    )
    if is_created:
        created += 1
    else:
        updated += 1

    tag = result["source"] or "查無資料"
    print(f"[{i+1}/{len(tickers)}] {ticker} -> {result['industry_name'] or '(無)'} ({tag})")

    if (i + 1) % 50 == 0:
        time.sleep(1)  # yfinance逐檔查，稍微降速避免打太快

print()
print(f"完成：新增 {created} 筆，更新 {updated} 筆，失敗 {len(failed)} 筆")
if failed:
    print("失敗清單:")
    for ticker, err in failed:
        print(f"  {ticker}: {err}")
