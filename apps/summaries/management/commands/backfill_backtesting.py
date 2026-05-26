"""
management command：對已有 outlook_calls 但還沒有 BacktestingRecord 的摘要補建回測列。

用法：
  python manage.py backfill_backtesting                  # 全部補（無 BacktestingRecord 的摘要）
  python manage.py backfill_backtesting --id 13          # 只補特定 summary id
  python manage.py backfill_backtesting --reprocess-skip # 重跑 ticker='' 的 skip 記錄
  python manage.py backfill_backtesting --dry-run        # 只印不寫入
"""
from django.core.management.base import BaseCommand

from apps.summaries.models import SummaryRecord, BacktestingRecord
from apps.summaries.services.backtesting import create_backtesting_rows, resolve_ticker, calc_end_time
from django.utils import timezone


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
        parser.add_argument(
            "--reprocess-skip", action="store_true",
            help="重新解析 ticker='' 的 skip 記錄（補完 TickerMap 後使用）",
        )

    def handle(self, *args, **options):
        target_id        = options["id"]
        dry_run          = options["dry_run"]
        reprocess_skip   = options["reprocess_skip"]

        if reprocess_skip:
            self._reprocess_skip(dry_run)
            return

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

    def _reprocess_skip(self, dry_run: bool):
        """重新解析 ticker='' 的 skip 記錄，補完 TickerMap 後呼叫。"""
        qs = (
            BacktestingRecord.objects.using("summariesdb")
            .filter(result="skip", ticker="")
            .exclude(asset="")
            .select_related("summary")
        )
        total = qs.count()
        self.stdout.write(f"ticker='' 的 skip 記錄：{total} 筆\n")

        updated = 0
        still_skip = 0

        for rec in qs.iterator():
            ticker = resolve_ticker(rec.asset)
            if not ticker:
                still_skip += 1
                continue

            start = rec.start_time
            end_time = calc_end_time(rec.timeframe_raw, start) if start and rec.timeframe_raw else None

            self.stdout.write(
                f"  ✓ {rec.asset:<20} → {ticker}"
                + (f"  end={end_time}" if end_time else "  end=無法解析")
            )

            if not dry_run:
                rec.ticker = ticker
                rec.result = "pending" if end_time else "skip"
                rec.end_time = end_time or rec.end_time
                rec.evaluated_at = None
                rec.save(using="summariesdb",
                         update_fields=["ticker", "result", "end_time", "evaluated_at"])
            updated += 1

        self.stdout.write("\n" + "=" * 60)
        mode = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            f"{mode}重跑完成：{updated} 筆轉為 pending，{still_skip} 筆仍 skip"
        )
