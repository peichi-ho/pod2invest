# scripts/populate_industry_classification_tw_full.py
"""
把台股全市場(TWSE上市+TPEx上櫃，直接來自官方開放資料，不用逐檔查yfinance)
灌進 IndustryClassification，把原本只有202檔(TickerMap子集)的範圍擴大成
全台股市場。美股/ETF/指數等非台股標的不受影響，不會被這支腳本動到。

用法：
  python scripts/populate_industry_classification_tw_full.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.summaries.models import IndustryClassification
from apps.summaries.services.industry_classification import (
    list_all_tw_tickers, TWSE_INDUSTRY_NAMES,
)

all_tw = list_all_tw_tickers()
print(f"台股全市場(上市+上櫃)共 {len(all_tw)} 檔")

created, updated = 0, 0
for ticker, code in all_tw:
    source = "twse" if ticker.endswith(".TW") else "tpex"
    obj, is_created = IndustryClassification.objects.using("summariesdb").update_or_create(
        ticker=ticker,
        defaults={
            "market": "TW",
            "industry_code": code,
            "industry_name": TWSE_INDUSTRY_NAMES.get(code, code),
            "sector_name": "",
            "source": source,
        },
    )
    if is_created:
        created += 1
    else:
        updated += 1

print(f"完成：新增 {created} 筆，更新 {updated} 筆")

total = IndustryClassification.objects.using("summariesdb").count()
print(f"IndustryClassification 總筆數: {total}")
