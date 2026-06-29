"""
對到期的 BacktestingRecord 抓股價並評估結果。

用法：
  python manage.py evaluate_backtesting            # 跑所有到期的 pending
  python manage.py evaluate_backtesting --dry-run  # 只印不寫入
  python manage.py evaluate_backtesting --all      # 含還未到期的也跑（用最新收盤價）
"""
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

import yfinance as yf
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.summaries.models import BacktestingRecord


# ── 方向正規化 ────────────────────────────────────────────────────────────────

def _normalize_direction(raw: str) -> Optional[str]:
    """回傳 'bullish' | 'bearish' | None"""
    r = (raw or "").strip().lower()
    if r in ("bullish", "看多", "buy", "long", "多"):
        return "bullish"
    if r in ("bearish", "看空", "sell", "short", "空"):
        return "bearish"
    return None  # neutral / 觀望 / 不明 → 無法評估


# ── 股價抓取 ──────────────────────────────────────────────────────────────────

_price_cache: dict[tuple, Optional[float]] = {}


def _get_close_price(ticker: str, target_date: date) -> Optional[float]:
    """
    抓 target_date 當天或最近一個交易日的收盤價。
    策略：
      1. 先往後找 7 天（處理假日、停牌）
      2. 若還是空（例如 target_date 是今天且收盤資料未更新、或遇到假日），
         改往前找 7 天，取最近的交易日收盤價
    查過的結果快取在 _price_cache，避免重複 API 呼叫。
    """
    key = (ticker, target_date)
    if key in _price_cache:
        return _price_cache[key]

    tk = yf.Ticker(ticker)

    def _fetch(start: date, end: date, take_last: bool = False) -> Optional[float]:
        try:
            hist = tk.history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                auto_adjust=True,
            )
            if hist.empty:
                return None
            price = float(hist["Close"].iloc[-1 if take_last else 0])
            return price
        except Exception:
            return None

    # 策略 1：往後找
    price = _fetch(target_date, target_date + timedelta(days=7))

    # 策略 2：往前找（假日/今天資料未出爐）
    if price is None:
        price = _fetch(target_date - timedelta(days=7), target_date, take_last=True)

    _price_cache[key] = price
    return price


# ── 評估單筆 ──────────────────────────────────────────────────────────────────

def _evaluate_record(rec: BacktestingRecord) -> str:
    """
    回傳新 result：'pass' | 'fail' | 'skip'
    """
    direction = _normalize_direction(rec.direction)
    if not direction:
        return "skip"  # neutral/觀望，無法評估

    start_price = _get_close_price(rec.ticker, rec.start_time)
    end_price   = _get_close_price(rec.ticker, rec.end_time)

    if start_price is None or end_price is None:
        return "skip"  # 抓不到價格

    rec.start_price = Decimal(str(round(start_price, 4)))
    rec.end_price   = Decimal(str(round(end_price, 4)))

    went_up = end_price > start_price
    if direction == "bullish":
        return "pass" if went_up else "fail"
    else:  # bearish
        return "pass" if not went_up else "fail"


# ── Management Command ────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = "對到期的 BacktestingRecord 抓股價並評估 pass/fail"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="只印結果，不寫入資料庫",
        )
        parser.add_argument(
            "--all", action="store_true",
            help="包含尚未到期的記錄（用最新收盤價評估）",
        )
        parser.add_argument(
            "--ticker", type=str, default=None,
            help="只評估指定 ticker（測試用）",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        include_all = options["all"]
        filter_ticker = options["ticker"]

        today = date.today()

        qs = BacktestingRecord.objects.using("summariesdb").filter(result="pending")
        if not include_all:
            qs = qs.filter(end_time__lte=today)
        if filter_ticker:
            qs = qs.filter(ticker=filter_ticker)

        records = list(qs.order_by("ticker", "start_time"))
        total = len(records)
        self.stdout.write(f"待評估：{total} 筆{'（含未到期）' if include_all else ''}\n")

        counts = {"pass": 0, "fail": 0, "skip": 0}
        no_price = []

        for i, rec in enumerate(records, 1):
            result = _evaluate_record(rec)
            counts[result] = counts.get(result, 0) + 1

            symbol = {"pass": "✓", "fail": "✗", "skip": "–"}.get(result, "?")
            sp = f"{rec.start_price}" if rec.start_price else "—"
            ep = f"{rec.end_price}"   if rec.end_price   else "—"
            self.stdout.write(
                f"[{i:4d}/{total}] {symbol} {rec.ticker:15s} "
                f"{rec.direction:8s} {str(rec.start_time)} → {str(rec.end_time)}  "
                f"{sp} → {ep}  ({result})"
            )

            if result == "skip" and not _normalize_direction(rec.direction):
                pass  # neutral，預期 skip
            elif result == "skip":
                no_price.append(f"{rec.ticker} ({rec.asset})")

            if not dry_run:
                rec.result = result
                rec.evaluated_at = timezone.now()
                rec.save(using="summariesdb")

            # 每 20 筆稍作停頓，避免 yfinance rate limit
            if i % 20 == 0:
                time.sleep(1)

        self.stdout.write("\n" + "=" * 60)
        mode = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            f"{mode}完成：pass {counts['pass']} / fail {counts['fail']} / skip {counts['skip']}"
        )
        if counts["pass"] + counts["fail"] > 0:
            accuracy = counts["pass"] / (counts["pass"] + counts["fail"]) * 100
            self.stdout.write(f"正確率：{accuracy:.1f}%（排除 skip）")
        if no_price:
            self.stdout.write(f"\n抓不到股價（{len(no_price)} 筆）：")
            for t in no_price[:20]:
                self.stdout.write(f"  {t}")
