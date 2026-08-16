"""
BacktestingRecord.ticker 是建立當下呼叫 resolve_ticker() 存死的值；resolve_ticker() 的規則
（_rule_based_ticker 的別名表、TickerMap 資料）會隨著時間被修正/調整，同一個股票名稱現在
重新解析可能得到不一樣的 ticker（例如「TESLA」→「TSLA」、「上詮」的 .TW→.TWO）。
這會導致使用者點試算時，ensure_stock_sentiment_score() 拿現在重新解析出來的 ticker
去比對，跟按鈕上寫死的舊 ticker 對不上，明明有討論內容卻被判定「找不到」。

這支指令把現有 pending 記錄的 ticker 用「現在」的 resolve_ticker() 重新算一次，
跟現有的 ticker 不一樣就更新回去，讓它對齊現在的解析邏輯。

用法：
  python manage.py resync_backtesting_tickers           # 實際更新
  python manage.py resync_backtesting_tickers --dry-run # 只印出會改什麼，不寫入
"""
from django.core.management.base import BaseCommand

from apps.summaries.models import BacktestingRecord
from apps.summaries.services.backtesting import resolve_ticker


class Command(BaseCommand):
    help = "用現在的 resolve_ticker() 重新解析 pending 的 BacktestingRecord.ticker，修復跟現況對不上的舊資料"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="只印出會改什麼，不寫入")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        qs = (
            BacktestingRecord.objects.using("summariesdb")
            .filter(result=BacktestingRecord.RESULT_PENDING)
            .only("id", "asset", "ticker", "episode_id")
        )

        total = 0
        updated = 0
        unresolvable_now = 0
        to_update = []

        for r in qs.iterator():
            total += 1
            fresh = resolve_ticker(r.asset)

            if not fresh:
                # 現在也解析不出來：不動它。就算改了也沒有意義——之後 find_stock_topics_with_fallback()
                # 一樣會因為 resolve_ticker(這支股票名稱) 是空字串而直接跳過，試算還是會顯示找不到資料。
                unresolvable_now += 1
                self.stdout.write(self.style.WARNING(
                    f"  [現在也解析不出來，不動] episode_id={r.episode_id} asset={r.asset!r} 舊ticker={r.ticker!r}"
                ))
                continue

            if fresh != r.ticker:
                self.stdout.write(
                    f"  episode_id={r.episode_id} asset={r.asset!r}  {r.ticker!r} → {fresh!r}"
                    + (" [dry-run]" if dry_run else "")
                )
                updated += 1
                if not dry_run:
                    r.ticker = fresh
                    to_update.append(r)

        if not dry_run and to_update:
            BacktestingRecord.objects.using("summariesdb").bulk_update(to_update, ["ticker"], batch_size=200)

        self.stdout.write(self.style.SUCCESS(
            f"\n完成：掃描 {total} 筆 pending 記錄，"
            f"{'預計' if dry_run else ''}修正 {updated} 筆，"
            f"{unresolvable_now} 筆現在也解析不出來（沒有動，這些之後試算應該還是會顯示找不到資料，"
            f"要修要靠 find_missing_tickers 那支指令去補 TickerMap）"
        ))
