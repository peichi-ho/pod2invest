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
  python manage.py backfill_thesis_scenarios --recheck --dry-run  # 用存好的 AI 原始輸出重跑程式檢查，只統計不寫入
  python manage.py backfill_thesis_scenarios --recheck            # 同上並寫回(不呼叫 Gemini，改了檢查規則時用)
  python manage.py backfill_thesis_scenarios --workers 6     # 預設就是兩步做法：Flash v6 拆 → Pro 逐一檢查，存成 v6
  python manage.py backfill_thesis_scenarios --no-review     # 只用 Flash、不做第二步檢查；--recheck 可以用 --prompt-version 指定資料版本
  python manage.py backfill_thesis_scenarios --re-review 現金流,配息 --workers 6   # 只重做第二步(用目前的檢查版本)
已經算過(同 prompt 版本、狀態不是 failed)的預設跳過，所以中斷後直接重跑就會接著做。
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError

from apps.summaries.models import BacktestingRecord, FinancialReportCache, ThesisScenarioExtraction
from apps.summaries.services.gemini import make_client
from apps.summaries.services.thesis_scenario import (
    PROMPT_VERSION,
    REVIEW_LOCATION,
    REVIEW_MODEL,
    REVIEW_VERSION,
    extract_thesis_scenarios,
    extract_two_step,
    re_review_extraction,
    find_grounded_stock_topics,
    recheck_thesis_scenarios,
    financial_sector_tickers,
    is_extractable_ticker,
    save_extraction,
)


def _reason_group(reason: str) -> str:
    """Pro 檢查的刪除原因每筆都是一句不同的說明，統計時只看規則代號(「Pro 檢查刪除（C）」)。"""
    return reason[:reason.index("）") + 1] if reason.startswith("Pro 檢查刪除") and "）" in reason else reason


class Command(BaseCommand):
    help = "把驗證句拆解成論點情境（ThesisScenarioExtraction，論點情境試算用）"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="最多呼叫幾次Gemini（測試用）")
        parser.add_argument("--ticker", type=str, default=None, help="只處理指定的股票代號")
        parser.add_argument("--redo", action="store_true", help="已經有同版本結果的也重算")
        parser.add_argument("--all-tickers", action="store_true", help="連沒有財報資料的股票也處理")
        parser.add_argument("--same-as", type=str, default=None,
                            help="只處理這個 prompt 版本已經跑過的驗證句（例如 v3），比較不同版本用")
        parser.add_argument("--recheck", action="store_true",
                            help="不呼叫 Gemini，用已存的 AI 原始輸出重跑程式端檢查（改了檢查規則時用）")
        parser.add_argument("--dry-run", action="store_true", help="只算會處理幾筆，不呼叫Gemini、不寫入")
        parser.add_argument("--prompt-version", default=PROMPT_VERSION,
                            help=f"拆解用的 prompt 版本，結果也存成這個版本(預設 {PROMPT_VERSION})")
        parser.add_argument("--review-model", default=REVIEW_MODEL,
                            help=f"兩步做法：拆完再用這個模型逐一檢查留或刪(預設 {REVIEW_MODEL})")
        parser.add_argument("--no-review", action="store_true", help="只用 Flash 拆、不做第二步檢查(v5 以前的做法)")
        parser.add_argument("--review-version", default=REVIEW_VERSION, help="檢查 prompt 版本(thesis_scenario.REVIEW_PROMPTS)")
        parser.add_argument("--review-location", default=REVIEW_LOCATION, help="檢查模型的 Vertex 區域(3.1 Pro 只在 global)")
        parser.add_argument("--workers", type=int, default=1, help="同時跑幾句(兩步做法每句約 20 秒)")
        parser.add_argument("--re-review", default=None, metavar="關鍵字,關鍵字",
                            help="只重做第二步：上次 Pro 刪除原因含這些關鍵字的那幾筆，用 --review-version 重新檢查"
                                 "(例如 --re-review 現金流,配息,殖利率)；填 ALL 代表全部重做")

    def handle(self, *args, **options):
        limit = options["limit"]
        dry_run = options["dry_run"]
        version = options["prompt_version"]
        if options["no_review"]:
            options["review_model"] = None
        if options["recheck"]:
            return self._recheck(dry_run, options["ticker"], limit, version)
        if options["re_review"]:
            return self._re_review(dry_run, version, options)

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
                .filter(prompt_version=version)
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
            + f"［版本 {version}{'，兩步：' + options['review_model'] if options['review_model'] else ''}］"
            + (" [dry-run]" if dry_run else "")
        )
        if dry_run:
            return
        if options["review_model"]:
            return self._two_step(groups, limit, version, options)

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

    def _save_with_retry(self, record, model: str, result: dict, version: str = PROMPT_VERSION, max_tries: int = 5):
        """
        寫入這一筆結果；Supabase 連線瞬斷時重連、等一下再試，不讓整個長時間批次停掉
        (第一次全量跑到第2109筆就因為「could not receive data from server」整個中斷)。
        重試完還是失敗才往外丟——已經寫入的不受影響，重跑指令會從沒做完的地方接著做。
        """
        for attempt in range(1, max_tries + 1):
            try:
                save_extraction(record, model, result, prompt_version=version)
                connections["summariesdb"].close_if_unusable_or_obsolete()
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

    def _two_step(self, groups: dict, limit, version: str, options):
        """兩步做法，--workers 句同時跑；每句跑完立刻寫入，中斷後重跑會從沒做完的接著做。"""
        model = os.getenv("THESIS_SCENARIO_MODEL", "gemini-2.5-flash")
        review_model, review_version = options["review_model"], options["review_version"]
        flash = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
        review = make_client(api_key=os.getenv("GEMINI_API_KEY", ""), location=options["review_location"])
        model_name = f"{model}+{review_model}:{review_version}"
        items = list(groups.items())[:limit] if limit else list(groups.items())
        counts = {"ok": 0, "no_driver": 0, "failed": 0}
        lock = threading.Lock()
        done = [0]
        t_start = time.time()

        def work(group):
            t0 = time.time()
            result = extract_two_step(flash, model, review, review_model, group[0],
                                      prompt_version=version, review_version=review_version)
            for r in group:
                self._save_with_retry(r, model_name, result, version)
            return group, result, time.time() - t0

        with ThreadPoolExecutor(max_workers=max(1, options["workers"])) as pool:
            futures = [pool.submit(work, group) for _, group in items]
            for fut in as_completed(futures):
                try:
                    group, result, secs = fut.result()
                except Exception as e:      # 寫入重試完還是失敗：記下來，重跑指令會補做
                    with lock:
                        counts["failed"] += 1
                        done[0] += 1
                    self.stdout.write(self.style.ERROR(f"  [{done[0]}/{len(items)}] 寫入失敗：{str(e)[:120]}"))
                    continue
                with lock:
                    counts[result["status"]] += 1
                    done[0] += 1
                    n = done[0]
                first = group[0]
                removed = sum(1 for d in result["dropped"] if str(d.get("reason", "")).startswith("Pro 檢查刪除"))
                note = {"ok": f"留 {len(result['scenarios'])} 個（Pro 刪 {removed}）", "no_driver": "無情境",
                        "failed": f"失敗：{result['error'][:80]}"}[result["status"]]
                eta = (time.time() - t_start) / n * (len(items) - n) / 60
                self.stdout.write(f"  [{n}/{len(items)}] {first.asset}({first.ticker}) {note} "
                                  f"({secs:.0f}s，剩約 {eta:.0f} 分)")
        self.stdout.write(self.style.SUCCESS(
            f"完成：有情境 {counts['ok']}、無情境 {counts['no_driver']}、失敗 {counts['failed']}"
            f"（失敗的重跑同一個指令會補做）"))

    def _re_review(self, dry_run: bool, version: str, options):
        """上次 Pro 刪除原因含關鍵字的那幾筆，只重做第二步(不重新呼叫 Flash)。"""
        words = [w for w in options["re_review"].split(",") if w]
        qs = ThesisScenarioExtraction.objects.using("summariesdb").filter(prompt_version=version).order_by("id")
        if options["ticker"]:
            qs = qs.filter(ticker=options["ticker"])
        if words == ["ALL"]:
            # 全部重做：已經用這個檢查版本做過的跳過，中斷後重跑會接著做
            done_tag = f":{options['review_version']}"
            rows = [r for r in qs.exclude(status=ThesisScenarioExtraction.STATUS_FAILED) if not r.model_name.endswith(done_tag)]
        else:
            rows = [r for r in qs if any(str(d.get("reason", "")).startswith("Pro 檢查刪除")
                                         and any(w in d["reason"] for w in words) for d in r.dropped)]
        self.stdout.write(f"{'全部' if words == ['ALL'] else '上次 Pro 刪除原因含「' + '、'.join(words) + '」的'} {len(rows)} 筆，用 {options['review_version']} 重新檢查"
                          + (" [dry-run]" if dry_run else ""))
        if dry_run or not rows:
            return
        review = make_client(api_key=os.getenv("GEMINI_API_KEY", ""), location=options["review_location"])
        model = os.getenv("THESIS_SCENARIO_MODEL", "gemini-2.5-flash")
        model_name = f"{model}+{options['review_model']}:{options['review_version']}"

        def work(row):
            try:
                new = re_review_extraction(row, review, options["review_model"], options["review_version"])
                if new is None:
                    return row, None, 0
                before = len(row.scenarios)
                row.status, row.scenarios, row.dropped, row.model_name = (new["status"], new["scenarios"],
                                                                           new["dropped"], model_name)
                row.save(using="summariesdb", update_fields=["status", "scenarios", "dropped", "model_name", "updated_at"])
                return row, new, before
            finally:
                connections["summariesdb"].close()

        restored = failed = 0
        with ThreadPoolExecutor(max_workers=max(1, options["workers"])) as pool:
            for i, fut in enumerate(as_completed([pool.submit(work, r) for r in rows]), 1):
                row, new, before = fut.result()
                if new is None:
                    failed += 1
                    self.stdout.write(self.style.WARNING(f"  [{i}/{len(rows)}] {row.asset_name} 檢查失敗，維持原本結果"))
                    continue
                restored += len(new["scenarios"]) - before
                self.stdout.write(f"  [{i}/{len(rows)}] {row.asset_name}：{before} → {len(new['scenarios'])} 個情境")
        self.stdout.write(self.style.SUCCESS(f"完成：情境淨增加 {restored} 個；失敗 {failed} 筆（維持原本結果，可再跑一次）"))

    def _recheck(self, dry_run: bool, ticker, limit, version: str = PROMPT_VERSION):
        """
        對這個 prompt 版本已存的結果，用 ai_scenarios 重跑程式端檢查，統計前後差異；
        不是 dry-run 才寫回。failed 的那幾筆沒有 AI 輸出，跳過(要重新呼叫 Gemini 才能補)。
        同一集的「個股」段落只查一次(topics_by_summary)，兩千多筆不用重查上萬次資料庫。
        """
        import json
        from apps.summaries.models import SummaryRecord

        qs = (ThesisScenarioExtraction.objects.using("summariesdb")
              .filter(prompt_version=version)
              .exclude(status=ThesisScenarioExtraction.STATUS_FAILED)
              .order_by("id"))
        if ticker:
            qs = qs.filter(ticker=ticker)
        rows = list(qs[:limit] if limit else qs)
        records = BacktestingRecord.objects.using("summariesdb").in_bulk([r.backtest_record_id for r in rows])
        # 摘要帶著整份 arguments JSON，一次撈上千集會讓 Supabase 連線中斷，分批每次 100 集
        summary_ids = sorted({r.summary_id for r in rows})
        summaries = {}
        for start in range(0, len(summary_ids), 100):
            summaries.update(SummaryRecord.objects.using("summariesdb")
                             .in_bulk(summary_ids[start:start + 100]))
        self.stdout.write(f"重算 {len(rows)} 筆（{len(summaries)} 集）" + (" [dry-run]" if dry_run else ""))

        def figures_filled(scenarios):
            return sum(1 for s in scenarios for v in (s.get("figures") or {}).values()
                       if v is not None and not isinstance(v, bool))

        topics_by_summary = {}
        changed = status_changed = 0
        filled_before = filled_after = 0
        reasons_before, reasons_after = {}, {}
        for i, row in enumerate(rows, 1):
            record = records.get(row.backtest_record_id)
            if record is None:
                continue
            record.summary = summaries[row.summary_id]
            if row.summary_id not in topics_by_summary:
                try:
                    topics_by_summary[row.summary_id] = find_grounded_stock_topics(record.summary)
                except Exception:
                    topics_by_summary[row.summary_id] = []
            new = recheck_thesis_scenarios(record, row.ai_scenarios, topics_by_summary[row.summary_id],
                                           prior_dropped=row.dropped)

            filled_before += figures_filled(row.scenarios)
            filled_after += figures_filled(new["scenarios"])
            for d in row.dropped:
                key = _reason_group(d["reason"])
                reasons_before[key] = reasons_before.get(key, 0) + 1
            for d in new["dropped"]:
                key = _reason_group(d["reason"])
                reasons_after[key] = reasons_after.get(key, 0) + 1

            same = json.dumps(new["scenarios"], sort_keys=True) == json.dumps(row.scenarios, sort_keys=True) \
                and json.dumps(new["dropped"], sort_keys=True) == json.dumps(row.dropped, sort_keys=True)
            if same:
                continue
            changed += 1
            status_changed += new["status"] != row.status
            if not dry_run:
                row.status, row.scenarios, row.dropped = new["status"], new["scenarios"], new["dropped"]
                for attempt in range(1, 6):
                    try:
                        row.save(using="summariesdb", update_fields=["status", "scenarios", "dropped", "updated_at"])
                        break
                    except OperationalError:
                        if attempt == 5:
                            raise
                        connections["summariesdb"].close()
                        time.sleep(min(5 * attempt, 30))
            if i % 200 == 0:
                self.stdout.write(f"  ...{i}/{len(rows)}")

        self.stdout.write(self.style.SUCCESS(
            f"有變動 {changed} 筆（其中狀態改變 {status_changed} 筆）；"
            f"有數字的欄位 {filled_before} → {filled_after}"
        ))
        for reason in sorted(set(reasons_before) | set(reasons_after)):
            self.stdout.write(f"  丟掉原因「{reason}」：{reasons_before.get(reason, 0)} → {reasons_after.get(reason, 0)}")
