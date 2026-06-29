"""
自動補齊 TickerMap 的 exchange 欄位並標記 verified。

規則層：.TW / .KS / .SS / ^ / =F / =X
yfinance層：純大寫英文美股，查 info['exchange']

用法：
  python manage.py verify_tickermap           # 跑所有未驗證
  python manage.py verify_tickermap --dry-run # 只印不寫入
"""
import re
import time

import yfinance as yf
from django.core.management.base import BaseCommand

from apps.summaries.models import TickerMap

# yfinance exchange 代號 → 統一名稱
_EXCHANGE_NORMALIZE = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ",
    "NYQ": "NYSE",   "ASE": "NYSE",   "PCX": "NYSE",
    "NYSEArca": "NYSE",
    "NASDAQ": "NASDAQ", "NYSE": "NYSE",
}


def _rule_exchange(ticker: str) -> str:
    """能用規則判斷的直接回傳，否則回傳空字串。"""
    if ticker.startswith("^"):
        return "INDEX"
    if ticker.endswith("=F"):
        return "FUTURES"
    if ticker.endswith("=X"):
        return "FOREX"
    if ticker.endswith(".TW"):
        return "TWSE"
    if ticker.endswith(".KS"):
        return "KRX"
    if ticker.endswith(".SS") or ticker.endswith(".SZ"):
        return "SSE"
    return ""


def _yfinance_exchange(ticker: str) -> str:
    """用 yfinance 查美股交易所，查不到回傳空字串。"""
    try:
        info = yf.Ticker(ticker).info
        raw = info.get("exchange") or info.get("fullExchangeName") or ""
        return _EXCHANGE_NORMALIZE.get(raw, raw)
    except Exception:
        return ""


class Command(BaseCommand):
    help = "自動補齊 TickerMap.exchange 並標記 verified"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="只印不寫入")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        qs = list(
            TickerMap.objects.using("summariesdb")
            .filter(verified=False)
            .order_by("ticker")
        )
        self.stdout.write(f"待處理：{len(qs)} 筆\n")

        ok = 0
        failed = []
        yf_calls = 0

        for i, row in enumerate(qs, 1):
            exchange = _rule_exchange(row.ticker)
            source = "rule"

            if not exchange:
                # 只對純大寫英文 ticker 打 yfinance
                if re.match(r"^[A-Z\-]{1,6}$", row.ticker):
                    exchange = _yfinance_exchange(row.ticker)
                    source = "yfinance"
                    yf_calls += 1
                    # 每 10 筆稍作停頓
                    if yf_calls % 10 == 0:
                        time.sleep(2)

            if exchange:
                status = "✓"
                ok += 1
                self.stdout.write(
                    f"[{i:3d}] {status} {row.ticker:<15} {row.asset_name:<15} "
                    f"→ {exchange:<10} ({source})"
                )
                if not dry_run:
                    row.exchange = exchange
                    row.verified = True
                    row.save(using="summariesdb", update_fields=["exchange", "verified"])
            else:
                status = "✗"
                failed.append(f"  {row.ticker:<15} {row.asset_name}")
                self.stdout.write(
                    f"[{i:3d}] {status} {row.ticker:<15} {row.asset_name:<15} "
                    f"→ 查不到"
                )

        self.stdout.write("\n" + "=" * 60)
        mode = "[DRY RUN] " if dry_run else ""
        self.stdout.write(f"{mode}完成：{ok} 筆成功，{len(failed)} 筆失敗")
        if failed:
            self.stdout.write("\n需手動補：")
            for f in failed:
                self.stdout.write(f)
