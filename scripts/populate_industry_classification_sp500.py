# scripts/populate_industry_classification_sp500.py
"""
把S&P500成分股(美股「主要範圍」代表清單，2026-09-30團隊決定用這個當美股的
全市場代表範圍，不是真的涵蓋全部美股——沒有像TWSE那樣的權威全市場API，
逐檔查6000多檔美股成本太高、價值也低)灌進 IndustryClassification。

成分股清單來源：datasets/s-and-p-500-companies(GitHub上維護的公開資料集，
定期跟著標普官方成分股調整更新)，只取Symbol欄位，分類還是走
classify_ticker()(yfinance)，不直接用清單裡的GICS Sector欄位，
確保跟資料庫裡其他美股用同一套分類系統、同一份中文對照表。

用法：
  python scripts/populate_industry_classification_sp500.py
"""
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.summaries.models import IndustryClassification
from apps.summaries.services.industry_classification import classify_ticker

_SP500_CSV_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"


def fetch_sp500_tickers() -> list[str]:
    res = requests.get(_SP500_CSV_URL, timeout=15)
    res.raise_for_status()
    lines = res.text.strip().splitlines()
    header = lines[0].split(",")
    symbol_idx = header.index("Symbol")
    tickers = []
    for line in lines[1:]:
        # Security欄位可能含逗號被引號包住，用csv模組正確解析
        import csv
        import io
        row = next(csv.reader(io.StringIO(line)))
        symbol = row[symbol_idx].strip().replace(".", "-")  # yfinance用 BRK-B 不是 BRK.B
        if symbol:
            tickers.append(symbol)
    return tickers


tickers = fetch_sp500_tickers()
print(f"S&P500成分股共 {len(tickers)} 檔")

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

    if (i + 1) % 20 == 0 or i == len(tickers) - 1:
        print(f"[{i+1}/{len(tickers)}] {ticker} -> {result['industry_name'] or '(無)'} ({result['source'] or '查無資料'})")

print()
print(f"完成：新增 {created} 筆，更新 {updated} 筆，失敗 {len(failed)} 筆")
if failed:
    print("失敗清單:")
    for ticker, err in failed:
        print(f"  {ticker}: {err}")

total = IndustryClassification.objects.using("summariesdb").count()
print(f"IndustryClassification 總筆數: {total}")
