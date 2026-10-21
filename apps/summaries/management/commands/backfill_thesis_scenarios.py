"""
management command：把驗證句(BacktestingRecord)拆解成論點情境，存進 ThesisScenarioExtraction
（論點情境試算用，見 apps/summaries/services/thesis_scenario.py）。

範圍：pro 版摘要、有 ticker、有論點文字、不是期貨/指數/加密貨幣的驗證句；預設只處理
「有財報資料(FinancialReportCache)」的股票——沒有財報的拆了也試算不了，先不花 API 費用。
銀行/壽險/券商不處理(獲利結構不適用營收→毛利→EPS，見 thesis_scenario.FINANCIAL_INDUSTRY_PATTERN)。
同一集同一檔同一句論點常常有好幾筆重複的驗證句，只呼叫一次 Gemini，結果複製給其他筆。

用法：
  python manage.py backfill_thesis_scenarios --dry-run        # 只算會處理幾筆，不呼叫Gemini、不寫入
  python manage.py backfill_thesis_scenarios --limit 20       # 只處理前20筆（測試用，控制API費用）
  python manage.py backfill_thesis_scenarios --ticker 2330.TW # 只處理特定股票
  python manage.py backfill_thesis_scenarios --redo           # 已經有同版本結果的也重算
  python manage.py backfill_thesis_scenarios --all-tickers    # 連沒有財報資料的股票也拆
  python manage.py backfill_thesis_scenarios --same-as v3     # 只處理 v3 已經跑過的那些驗證句(比較版本用)
已經算過(同 prompt 版本、狀態不是 failed)的預設跳過，所以中斷後直接重跑就會接著做。
"""
import os
import time

from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError

from apps.summaries.models import BacktestingRecord, FinancialReportCache, ThesisScenarioExtraction
from apps.summaries.services.gemini import make_client
from apps.summaries.services.thesis_scenario import (
    PROMPT_VERSION,
    extract_thesis_scenarios,
    financial_sector_tickers,
    is_extractable_ticker,
)


class Command(BaseCommand):
    help = "把驗證句拆解成論點情境（ThesisScenarioExtraction，論點情境試算用）"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="最多呼叫幾次Gemini（測試用）")
        parser.add_argument("--ticker", type=str, default=None, help="只處理指定的股票代號")
        parser.add_argument("--redo", action="store_true", help="已經有同版本結果的也重算")
        parser.add_argument("--all-tickers", action="store_true", help="連沒有財報資料的股票也處理")
        parser.add_argument("--same-as", type=str, default=None,
                            help="只處理這個 prompt 版本已經跑過的驗證句（例如 v3），比較不同版本用")
        parser.add_argument("--dry-run", action="store_true", help="只算會處理幾筆，不呼叫Gemini、不寫入")

    def handle(self, *args, **options):
        limit = options["limit"]
        dry_run = options["dry_run"]

        qs = (
            BacktestingRecord.objects.using("summariesdb")
            .filter(summary__mode="pro")
            .exclude(ticker="")
            .exclude(thesis="")
            # 不 select_related("summary")：摘要帶著整份 arguments JSON，兩千多筆一次撈會讓
            # Supabase 連線中斷(實測發生過)。只有真的要呼叫 Gemini 的那幾筆才逐筆讀摘要。
            .order_by("id")
        )
        if options["ticker"]:
            qs = qs.filter(ticker=options["ticker"])
        if not options["all_tickers"]:
            fin_tickers = set(
                FinancialReportCache.objects.using("summariesdb").values_list("ticker", flat=True).distinct()
            )
            qs = qs.filter(ticker__in=fin_tickers)
        if options["same_as"]:
            qs = qs.filter(id__in=ThesisScenarioExtraction.objects.using("summariesdb")
                           .filter(prompt_version=options["same_as"]).values("backtest_record_id"))
        financial = financial_sector_tickers()
        records = [r for r in qs if is_extractable_ticker(r.ticker) and r.ticker not in financial]

        done_ids = set()
        if not options["redo"]:
            done_ids = set(
                ThesisScenarioExtraction.objects.using("summariesdb")
                .filter(prompt_version=PROMPT_VERSION)
                .exclude(status=ThesisScenarioExtraction.STATUS_FAILED)
                .values_list("backtest_record_id", flat=True)
            )
        todo = [r for r in records if r.id not in done_ids]

        # 同一集(summary)同一檔同一句論點只呼叫一次，結果複製給其他重複的驗證句
        groups = {}
        for r in todo:
            groups.setdefault((r.summary_id, r.ticker, r.thesis.strip()), []).append(r)

        self.stdout.write(
            f"符合條件 {len(records)} 筆，已完成 {len(records) - len(todo)} 筆，"
            f"待處理 {len(todo)} 筆（去重後要呼叫 Gemini {len(groups)} 次）"
            + (" [dry-run]" if dry_run else "")
        )
        if dry_run:
            return

        model = os.getenv("THESIS_SCENARIO_MODEL", "gemini-2.5-flash")
        client = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
        counts = {"ok": 0, "no_driver": 0, "failed": 0}

        for i, ((_, ticker, _), group) in enumerate(groups.items(), 1):
            if limit and i > limit:
                break
            first = group[0]
            t0 = time.time()
            result = extract_thesis_scenarios(client, model, first)
            counts[result["status"]] += 1

            for r in group:
                self._save_with_retry(r, model, result)

            note = {
                "ok": f"{len(result['scenarios'])} 個情境",
                "no_driver": "無具體驅動因素",
                "failed": f"失敗：{result['error'][:80]}",
            }[result["status"]]
            dup = f"（含重複 {len(group) - 1} 筆）" if len(group) > 1 else ""
            self.stdout.write(f"  [{i}/{len(groups)}] {first.asset}({ticker}) {note}{dup} ({time.time() - t0:.1f}s)")

        self.stdout.write(self.style.SUCCESS(
            f"完成：有情境 {counts['ok']}、無驅動因素 {counts['no_driver']}、失敗 {counts['failed']}"
        ))

    def _save_with_retry(self, record, model: str, result: dict, max_tries: int = 5):
        """
        寫入這一筆結果；Supabase 連線瞬斷時重連、等一下再試，不讓整個長時間批次停掉
        (第一次全量跑到第2109筆就因為「could not receive data from server」整個中斷)。
        重試完還是失敗才往外丟——已經寫入的不受影響，重跑指令會從沒做完的地方接著做。
        """
        for attempt in range(1, max_tries + 1):
            try:
                ThesisScenarioExtraction.objects.using("summariesdb").update_or_create(
                    backtest_record_id=record.id,
                    prompt_version=PROMPT_VERSION,
                    defaults={
                        "summary_id": record.summary_id,
                        "ticker": record.ticker,
                        "asset_name": record.asset,
                        "model_name": model,
                        "status": result["status"],
                        "scenarios": result["scenarios"],
                        "ai_scenarios": result["ai_scenarios"],
                        "dropped": result["dropped"],
                        "no_driver_reason": result["no_driver_reason"],
                        "error": result["error"],
                    },
                )
                return
            except OperationalError as e:
                if attempt == max_tries:
                    raise
                wait = min(5 * attempt, 30)
                self.stdout.write(self.style.WARNING(
                    f"  [資料庫連線錯誤，{wait}秒後重試 {attempt}/{max_tries}] {str(e)[:120]}"
                ))
                connections["summariesdb"].close()
                time.sleep(wait)
