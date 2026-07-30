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

        qs = SummaryRecord.objects.using("summariesdb").exclude(arguments=[]).exclude(published_at=None)
        if target_id:
            qs = qs.filter(id=target_id)
        else:
            qs = qs.order_by("published_at")
        if limit:
            qs = qs[:limit]

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

        for record in qs.iterator():
            total_records += 1
            topics = find_grounded_stock_topics(record)
            if not topics:
                skipped_no_topic += 1
                continue

            for t in topics:
                asset_name, ticker, stock_topic = t["asset_name"], t["ticker"], t["topic"]

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

        self.stdout.write(self.style.SUCCESS(
            f"\n完成：掃描 {total_records} 筆摘要，"
            f"{'預計' if dry_run else ''}新建 {created} 筆分數，"
            f"{skipped_existing} 筆已存在跳過，"
            f"{skipped_no_topic} 筆無合法個股段落，"
            f"{skipped_no_price_data} 筆抓不到股價，"
            f"{classify_failed} 筆分類失敗"
        ))
