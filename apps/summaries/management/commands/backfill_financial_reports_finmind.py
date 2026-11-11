# apps/summaries/management/commands/backfill_financial_reports_finmind.py
"""
用 FinMind 把「台股」的季度損益表歷史補進 FinancialReportCache(summariesdb)。

為什麼需要這支：yfinance 的季報只回最近約 5 季，不夠算「5 年窗口起點與終點各一個近四季
EPS」(至少約 24 季)。FinMind 的台股損益表從 2015 年起就有，沒帶 token 也拿得到。

跟 backfill_financial_reports.py(yfinance版)的差別：
  - 只處理台股(.TW/.TWO)，其他代號會跳過；
  - data_source="finmind"，EPS 是公司公告的「基本每股盈餘」，不是yfinance的稀釋EPS；
  - FinMind 沒有公告日欄位，disclosure_date 用「期末＋45天(年報＋90天)」估計；
  - 同一檔股票的同一期，FinMind 的資料會覆蓋 yfinance 版本(update_or_create)，避免同一檔
    股票裡混用兩種EPS口徑。

用法：
    python manage.py backfill_financial_reports_finmind 2330.TW 2317.TW
    python manage.py backfill_financial_reports_finmind --top 40          # 依被討論次數排序的前40檔台股
    python manage.py backfill_financial_reports_finmind --top 40 --dry-run  # 只抓不寫入
"""
import time
from collections import defaultdict
from datetime import date, timedelta

import requests
from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.summaries.models import FinancialReportCache, StockSentimentScore

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _assumed_disclosure_lag_days(period_end: date) -> int:
    """台灣季報約45天內須公告，年報(Q4)約3個月。"""
    return 90 if period_end.month == 12 else 45


def _is_tw_stock(ticker: str) -> bool:
    return ticker.upper().endswith((".TW", ".TWO"))


def _stock_id(ticker: str) -> str:
    return ticker.split(".")[0]


def fetch_finmind_income_statement(stock_id: str, start_date: str, end_date: str) -> dict:
    """回傳 {期末日(date): {欄位type: 數值}}。單季數值(不是累計)，單位是新台幣元。"""
    resp = requests.get(
        FINMIND_URL,
        params={"dataset": "TaiwanStockFinancialStatements", "data_id": stock_id,
                "start_date": start_date, "end_date": end_date},
        timeout=40,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != 200:
        raise RuntimeError(f"FinMind 回傳 status={payload.get('status')} msg={payload.get('msg')}")
    pivot = defaultdict(dict)
    for r in payload.get("data", []):
        try:
            pivot[date.fromisoformat(r["date"])][r["type"]] = r["value"]
        except (KeyError, ValueError):
            continue
    return pivot


class Command(BaseCommand):
    help = "用 FinMind 補台股季度損益表歷史進 FinancialReportCache"

    def add_arguments(self, parser):
        parser.add_argument("tickers", nargs="*", help="股票代號，例如 2330.TW 2317.TW")
        parser.add_argument("--top", type=int, default=0,
                            help="改成依被討論次數(pro版紀錄)取前N檔台股，不用列代號")
        parser.add_argument("--start", default="2015-01-01", help="起始日期，預設 2015-01-01")
        parser.add_argument("--sleep", type=float, default=1.0, help="每檔之間暫停秒數，預設 1.0")
        parser.add_argument("--dry-run", action="store_true", help="只抓取並顯示，不寫入資料庫")

    def handle(self, *args, **opts):
        tickers = [t for t in opts["tickers"] if _is_tw_stock(t)]
        skipped = [t for t in opts["tickers"] if not _is_tw_stock(t)]
        for t in skipped:
            self.stderr.write(f"跳過 {t}：FinMind 只有台股資料")

        if opts["top"]:
            rows = (
                StockSentimentScore.objects.using("summariesdb")
                .filter(summary__mode="pro")
                .values("ticker").annotate(n=Count("id")).order_by("-n")
            )
            picked = [r["ticker"] for r in rows if _is_tw_stock(r["ticker"])][: opts["top"]]
            tickers = list(dict.fromkeys(tickers + picked))

        if not tickers:
            self.stderr.write("沒有可處理的台股代號（用代號列表或 --top N）")
            return

        end = date.today().isoformat()
        total_periods = 0
        ok = 0
        for i, ticker in enumerate(tickers, 1):
            try:
                stm = fetch_finmind_income_statement(_stock_id(ticker), opts["start"], end)
            except Exception as e:
                self.stderr.write(f"[{i}/{len(tickers)}] {ticker}: 抓取失敗 {e}")
                time.sleep(opts["sleep"])
                continue

            n = 0
            eps_count = 0
            for period_end, v in sorted(stm.items()):
                revenue = v.get("Revenue")
                if revenue is None:
                    continue
                eps = v.get("EPS")
                eps_count += 1 if eps is not None else 0
                values = {
                    "revenue": revenue,
                    "gross_profit": v.get("GrossProfit"),
                    "operating_income": v.get("OperatingIncome"),
                    "ebit": v.get("OperatingIncome"),   # FinMind 沒有 EBIT，用營業利益近似
                    "net_income": v.get("IncomeAfterTaxes"),
                    "eps": eps,
                    "shares_outstanding": None,         # 這個資料集沒有股數，EPS 已直接提供
                }
                if not opts["dry_run"]:
                    FinancialReportCache.objects.using("summariesdb").update_or_create(
                        ticker=ticker, fiscal_period_end=period_end,
                        defaults={
                            "data_source": "finmind",
                            "disclosure_date": period_end + timedelta(days=_assumed_disclosure_lag_days(period_end)),
                            **values,
                        },
                    )
                n += 1

            total_periods += n
            ok += 1 if n else 0
            first = min(stm) if stm else None
            last = max(stm) if stm else None
            self.stdout.write(
                f"[{i}/{len(tickers)}] {ticker}: {n} 期（{first} ~ {last}），其中有 EPS 的 {eps_count} 期"
                + ("（dry-run，未寫入）" if opts["dry_run"] else "")
            )
            time.sleep(opts["sleep"])

        self.stdout.write(self.style.SUCCESS(
            f"完成：{ok}/{len(tickers)} 檔有資料，共 {total_periods} 期" + ("（dry-run）" if opts["dry_run"] else "")
        ))
