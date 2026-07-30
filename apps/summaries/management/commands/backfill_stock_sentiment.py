"""
management command：對已有「個股」段落但還沒有 StockSentimentScore 的摘要，
分類出 risk_score / macro_score 並補建。

用法：
  python manage.py backfill_stock_sentiment                 # 全部補
  python manage.py backfill_stock_sentiment --id 5129       # 只補特定 summary id
  python manage.py backfill_stock_sentiment --limit 20      # 只處理前20筆摘要（測試用，控制API費用）
  python manage.py backfill_stock_sentiment --dry-run       # 只印不寫入、不呼叫Gemini
"""
import os

from django.core.management.base import BaseCommand

from apps.summaries.models import SummaryRecord, StockSentimentScore
from apps.summaries.services.gemini import make_client
from apps.summaries.services.sentiment_score import (
    find_grounded_stock_topics,
    get_base_vol_asof,
    classify_stock_sentiment,
)


class Command(BaseCommand):
    help = "補建 StockSentimentScore（個股專屬 risk_score/macro_score，試算計算機用）"

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, default=None, help="只補指定的 summary id")
        parser.add_argument("--limit", type=int, default=None, help="最多處理幾筆摘要（測試用）")
        parser.add_argument("--dry-run", action="store_true", help="只印出會處理什麼，不呼叫Gemini、不寫入")

    def handle(self, *args, **options):
        target_id = options["id"]
        limit = options["limit"]
        dry_run = options["dry_run"]
        BATCH_SIZE = 50

        def fetch_batches():
            """
            用 id 分批抓取（keyset pagination），不要一次把全部摘要撈進記憶體：
            - 一次撈全部曾經因為 SQL 太慢被 Supabase 逾時取消（exclude(arguments=[]) 要
              比對整個 JSONB，掃五千多列很慢）。
            - 用 .only() 只抓需要的欄位，減少傳輸量。
            - arguments=[] 的排除改在 Python 端做，不讓資料庫比對大型 JSONB。
            """
            if target_id:
                qs = (SummaryRecord.objects.using("summariesdb")
                      .only("id", "arguments", "published_at", "episode_id")
                      .filter(id=target_id))
                yield list(qs)
                return

            last_id = 0
            fetched_total = 0
            while True:
                batch = list(
                    SummaryRecord.objects.using("summariesdb")
                    .only("id", "arguments", "published_at", "episode_id")
                    .filter(id__gt=last_id)
                    .order_by("id")[:BATCH_SIZE]
                )
                if not batch:
                    return
                last_id = batch[-1].id
                yield batch
                fetched_total += len(batch)
                if limit and fetched_total >= limit:
                    return

        client = None
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        if not dry_run:
            client = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))

        total_records = 0
        created = 0
        skipped_no_topic = 0
        skipped_existing = 0
        skipped_no_price_data = 0
        classify_failed = 0
        errored = 0

        processed_this_run = 0
        for batch in fetch_batches():
          for record in batch:
            if not record.arguments or not record.published_at:
                continue
            if limit and processed_this_run >= limit:
                break
            processed_this_run += 1
            total_records += 1
            topics = find_grounded_stock_topics(record)
            if not topics:
                skipped_no_topic += 1
                continue

            for t in topics:
                asset_name, ticker, stock_topic = t["asset_name"], t["ticker"], t["topic"]

                try:
                    if StockSentimentScore.objects.using("summariesdb").filter(
                        summary=record, asset_name=asset_name
                    ).exists():
                        skipped_existing += 1
                        continue

                    self.stdout.write(
                        f"  summary id={record.id}  {asset_name}({ticker})"
                        + (" [dry-run]" if dry_run else "")
                    )
                    if dry_run:
                        continue

                    bv = get_base_vol_asof(ticker, record.published_at)
                    if not bv:
                        self.stdout.write(f"    [跳過] 抓不到 {ticker} 當時的歷史股價")
                        skipped_no_price_data += 1
                        continue
                    base, annual_vol = bv

                    result = classify_stock_sentiment(client, model, record, asset_name, ticker, stock_topic)
                    if not result:
                        self.stdout.write(f"    [跳過] Gemini 分類失敗")
                        classify_failed += 1
                        continue

                    StockSentimentScore.objects.using("summariesdb").create(
                        summary=record,
                        episode_id=record.episode_id,
                        asset_name=asset_name,
                        ticker=ticker,
                        base=base,
                        annual_vol=annual_vol,
                        macro_score=result["macro_score"],
                        risk_score=result["risk_score"],
                        rationale=result["rationale"],
                    )
                    created += 1
                except Exception as e:
                    # 單筆失敗（例如連線瞬斷）不該讓整個長時間任務中斷、丟失前面已寫入的進度。
                    self.stdout.write(self.style.WARNING(
                        f"    [錯誤，跳過此筆] {asset_name}({ticker}): {e}"
                    ))
                    errored += 1
                    from django.db import connections
                    connections["summariesdb"].close_if_unusable_or_obsolete()
                    continue

        self.stdout.write(self.style.SUCCESS(
            f"\n完成：掃描 {total_records} 筆摘要，"
            f"{'預計' if dry_run else ''}新建 {created} 筆分數，"
            f"{skipped_existing} 筆已存在跳過，"
            f"{skipped_no_topic} 筆無合法個股段落，"
            f"{skipped_no_price_data} 筆抓不到股價，"
            f"{classify_failed} 筆分類失敗，"
            f"{errored} 筆發生錯誤（已跳過，重新執行同一指令會自動補上，不會重複算已成功的）"
        ))
