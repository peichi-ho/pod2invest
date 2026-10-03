# apps/summaries/management/commands/backfill_financial_reports.py
"""
從 yfinance 抓季度財報資料，寫進 FinancialReportCache(summariesdb)，
給 apps/calculator/services/earnings_simulator.py 的 Monte Carlo 引擎當
抽樣母體用。

disclosure_date（這一期財報實際公告日）的抓法跟
apps/knowledge_graph/services/financial_data.py 的 _nearest_disclosure_date /
_assumed_disclosure_lag_days 完全一致，故意重複一份而不是共用，因為那邊
在 knowledge_graph app，跟這裡不同 app_label 會導致 DB 路由跑錯資料庫。

用法：
    python manage.py backfill_financial_reports 2330.TW 2454.TW NVDA
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from apps.summaries.models import FinancialReportCache

# 左邊是 FinancialReportCache 的欄位名稱，右邊是 yfinance quarterly_income_stmt
# 裡對應的欄位名稱(index)。
FIELD_MAP = {
    "revenue": "Total Revenue",
    "gross_profit": "Gross Profit",
    "operating_income": "Operating Income",
    "ebit": "EBIT",
    "net_income": "Net Income",
    "shares_outstanding": "Diluted Average Shares",
    "eps": "Diluted EPS",
}


def _assumed_disclosure_lag_days(period_end: date) -> int:
    """台灣規定季報約45天內須公告，年報(Q4)約3個月。用期末月份判斷。"""
    return 90 if period_end.month == 12 else 45


def _nearest_disclosure_date(earnings_dates, period_end: date) -> date | None:
    """在 t.earnings_dates(公告日→財報內容表)中找期末日之後最近一筆公告日。"""
    if earnings_dates is None or earnings_dates.empty:
        return None
    try:
        candidates = [d.date() for d in earnings_dates.index if d.date() >= period_end]
        return min(candidates) if candidates else None
    except Exception:
        return None


class Command(BaseCommand):
    help = "從 yfinance 抓季度財報資料，寫進 FinancialReportCache"

    def add_arguments(self, parser):
        parser.add_argument("tickers", nargs="+", help="股票代號，例如 2330.TW NVDA")

    def handle(self, *args, **options):
        import yfinance as yf

        for ticker in options["tickers"]:
            self.stdout.write(f"處理 {ticker} ...")
            t = yf.Ticker(ticker)
            try:
                qis = t.quarterly_income_stmt
            except Exception as e:
                self.stderr.write(f"  抓取失敗: {e}")
                continue

            if qis is None or qis.empty:
                self.stderr.write(f"  {ticker} 沒有季度財報資料")
                continue

            try:
                earnings_dates = t.earnings_dates
            except Exception:
                earnings_dates = None

            n_written = 0
            n_real_date = 0
            for period_end_ts in qis.columns:
                period_end = period_end_ts.date()
                row = {}
                for field, yf_key in FIELD_MAP.items():
                    if yf_key not in qis.index:
                        row[field] = None
                        continue
                    val = qis.loc[yf_key, period_end_ts]
                    # val != val 用來排除 NaN(pandas/numpy的NaN不等於自己)
                    row[field] = float(val) if val is not None and val == val else None

                if row.get("revenue") is None:
                    continue  # 沒有營收這筆資料就沒有意義，跳過這一期

                disclosure_date = _nearest_disclosure_date(earnings_dates, period_end)
                if disclosure_date is not None:
                    n_real_date += 1
                else:
                    disclosure_date = period_end + timedelta(days=_assumed_disclosure_lag_days(period_end))

                FinancialReportCache.objects.using("summariesdb").update_or_create(
                    ticker=ticker,
                    fiscal_period_end=period_end,
                    defaults={"data_source": "yfinance", "disclosure_date": disclosure_date, **row},
                )
                n_written += 1

            self.stdout.write(self.style.SUCCESS(
                f"  {ticker}: 寫入 {n_written} 期（{n_real_date} 期有真實公告日，"
                f"{n_written - n_real_date} 期用估計值）"
            ))
