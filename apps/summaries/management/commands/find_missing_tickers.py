"""
找出 BacktestingRecord 中 ticker='' 的 asset，
嘗試用 yfinance 自動解析，成功的直接寫入 TickerMap。

用法：
  python manage.py find_missing_tickers           # 自動解析 + 列出無法解析的
  python manage.py find_missing_tickers --dry-run # 只印不寫入
"""
import time

import yfinance as yf
from django.core.management.base import BaseCommand
from django.db import connections

from apps.summaries.models import BacktestingRecord, TickerMap


def _try_yfinance(asset: str) -> tuple[str, str]:
    """
    嘗試用 yfinance 搜尋 asset 名稱，回傳 (ticker, exchange)。
    查不到回傳 ('', '')。
    """
    try:
        results = yf.Search(asset, max_results=1).quotes
        if not results:
            return "", ""
        q = results[0]
        ticker = q.get("symbol", "")
        exchange = q.get("exchange", "") or q.get("fullExchangeName", "")
        return ticker, exchange
    except Exception:
        return "", ""


class Command(BaseCommand):
    help = "找出 ticker='' 的 BacktestingRecord asset，嘗試自動補齊 TickerMap"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="只印不寫入")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # 找出所有 ticker='' 的 asset（去重）
        missing = (
            BacktestingRecord.objects.using("summariesdb")
            .filter(ticker="", result="skip")
            .exclude(asset="")
            .values_list("asset", flat=True)
            .distinct()
        )
        assets = sorted(set(missing))

        # 排除已在 TickerMap 的（可能只是 asset_name 不同）
        existing_assets = set(
            TickerMap.objects.using("summariesdb")
            .values_list("asset_name", flat=True)
        )
        to_check = [a for a in assets if a not in existing_assets]

        self.stdout.write(f"ticker='' 的 asset：{len(assets)} 種，其中 {len(to_check)} 種不在 TickerMap\n")

        resolved = []
        unresolved = []

        for i, asset in enumerate(to_check, 1):
            ticker, exchange = _try_yfinance(asset)
            if ticker:
                self.stdout.write(f"  ✓ [{i:3d}] {asset:<20} → {ticker} ({exchange})")
                resolved.append((asset, ticker, exchange))
            else:
                self.stdout.write(f"  ✗ [{i:3d}] {asset:<20} → 查不到")
                unresolved.append(asset)

            if i % 10 == 0:
                time.sleep(1)

        # 寫入 TickerMap
        if resolved and not dry_run:
            self.stdout.write(f"\n寫入 TickerMap：{len(resolved)} 筆")
            with connections["summariesdb"].cursor() as c:
                for asset, ticker, exchange in resolved:
                    c.execute(
                        "SELECT id FROM ticker_map WHERE asset_name=%s", [asset]
                    )
                    if not c.fetchone():
                        c.execute(
                            "INSERT INTO ticker_map "
                            "(asset_name, ticker, exchange, sector, verified, zh_name, created_at) "
                            "VALUES (%s,%s,%s,'','',''  ,NOW())",
                            [asset, ticker, exchange]
                        )
                        self.stdout.write(f"  新增 {ticker:<12} {asset}")

        self.stdout.write("\n" + "=" * 60)
        mode = "[DRY RUN] " if dry_run else ""
        self.stdout.write(f"{mode}自動解析：{len(resolved)} 筆 ✓")
        if unresolved:
            self.stdout.write(f"無法解析（需手動補）：{len(unresolved)} 筆")
            for a in unresolved:
                # 顯示這個 asset 出現在哪些集數
                episodes = list(
                    BacktestingRecord.objects.using("summariesdb")
                    .filter(asset=a, ticker="")
                    .values_list("summary__source_filename", flat=True)
                    .distinct()[:3]
                )
                self.stdout.write(f"  {a:<25} 出現於：{', '.join(str(e) for e in episodes)}")
