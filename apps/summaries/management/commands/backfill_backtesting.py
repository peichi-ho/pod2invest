"""
management command：對已有 outlook_calls 但還沒有 BacktestingRecord 的摘要補建回測列。

用法：
  python manage.py backfill_backtesting            # 全部補
  python manage.py backfill_backtesting --id 13    # 只補特定 summary id
  python manage.py backfill_backtesting --dry-run  # 只印不寫入
"""
from django.core.management.base import BaseCommand

from apps.summaries.models import SummaryRecord
from apps.summaries.services.backtesting import create_backtesting_rows


class Command(BaseCommand):
    help = "補建 BacktestingRecord（從已儲存的 outlook_calls 欄位）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--id", type=int, default=None,
            help="只補指定的 summary id",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="只印出會建多少列，不實際寫入",
        )

    def handle(self, *args, **options):
        target_id = options["id"]
        dry_run   = options["dry_run"]

        qs = SummaryRecord.objects.using("summariesdb")

        if target_id:
            qs = qs.filter(id=target_id)
        else:
            # 只補「outlook_calls 不是空的，且還沒有任何 BacktestingRecord」的摘要
            qs = qs.exclude(outlook_calls=[]).filter(
                backtesting_records__isnull=True
            )

        total = qs.count()
        self.stdout.write(f"找到 {total} 筆摘要需要補建回測列")

        created = 0
        skipped = 0

        for record in qs.iterator():
            calls = record.outlook_calls or []
            if not calls:
                skipped += 1
                continue

            self.stdout.write(
                f"  summary id={record.id}  outlook_calls={len(calls)} 筆"
                + (" [dry-run]" if dry_run else "")
            )

            if not dry_run:
                create_backtesting_rows(
                    summary_record=record,
                    episode_id=record.episode_id,
                    outlook_calls=calls,
                    published_at=record.published_at,
                )

            created += len(calls)

        self.stdout.write(
            self.style.SUCCESS(
                f"\n完成：處理 {total} 筆摘要，"
                f"{'預計' if dry_run else ''}建立 {created} 筆回測列，"
                f"跳過 {skipped} 筆（無 outlook_calls）"
            )
        )
