"""
重新計算每位講者的預測準確率，按「全體」與「產業（sector）」分組，
寫入 speaker_accuracy 資料表。

邏輯：
  - result IN ('pass','fail','skip') 的記錄才計入
  - 全體：podcaster 維度，不限 sector
  - 產業：podcaster × sector（來自 ticker_map.sector），
    sector='' 代表全體，其他為具體產業名稱
  - 每次執行整批 DELETE + INSERT，確保資料是最新快照

用法：
  python manage.py refresh_speaker_accuracy          # 重算並寫入
  python manage.py refresh_speaker_accuracy --dry-run  # 只印不寫入
"""
from django.core.management.base import BaseCommand
from django.db import connections

from apps.summaries.models import SpeakerAccuracy


# ── SQL ─────────────────────────────────────────────────────────────────────

# 全體準確率（sector = ''）
_SQL_OVERALL = """
SELECT
    sr.podcaster,
    '' AS sector,
    SUM(CASE WHEN b.result = 'pass' THEN 1 ELSE 0 END) AS pass_count,
    SUM(CASE WHEN b.result = 'fail' THEN 1 ELSE 0 END) AS fail_count,
    SUM(CASE WHEN b.result = 'skip' THEN 1 ELSE 0 END) AS skip_count,
    SUM(CASE WHEN b.result IN ('pass','fail') THEN 1 ELSE 0 END) AS evaluatable
FROM backtesting b
JOIN summaries_summaryrecord sr ON b.summary_id = sr.id
WHERE b.result IN ('pass','fail','skip')
  AND sr.podcaster != ''
GROUP BY sr.podcaster
ORDER BY sr.podcaster;
"""

# 分產業準確率（sector 取自 ticker_map；無法對應的略去）
_SQL_BY_SECTOR = """
SELECT
    sr.podcaster,
    tm_agg.sector,
    SUM(CASE WHEN b.result = 'pass' THEN 1 ELSE 0 END) AS pass_count,
    SUM(CASE WHEN b.result = 'fail' THEN 1 ELSE 0 END) AS fail_count,
    SUM(CASE WHEN b.result = 'skip' THEN 1 ELSE 0 END) AS skip_count,
    SUM(CASE WHEN b.result IN ('pass','fail') THEN 1 ELSE 0 END) AS evaluatable
FROM backtesting b
JOIN summaries_summaryrecord sr ON b.summary_id = sr.id
JOIN (
    SELECT ticker, MAX(sector) AS sector
    FROM ticker_map
    WHERE ticker != '' AND sector != ''
    GROUP BY ticker
) tm_agg ON b.ticker = tm_agg.ticker
WHERE b.result IN ('pass','fail','skip')
  AND sr.podcaster != ''
GROUP BY sr.podcaster, tm_agg.sector
ORDER BY sr.podcaster, tm_agg.sector;
"""


def _calc_accuracy(pass_count: int, evaluatable: int):
    if evaluatable == 0:
        return None
    return round(pass_count / evaluatable, 4)


class Command(BaseCommand):
    help = "重算講者預測準確率（全體 + 分產業），寫入 speaker_accuracy 表"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="只印不寫入")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        rows_to_save: list[dict] = []

        with connections["summariesdb"].cursor() as c:
            # ── 1. 全體 ──
            c.execute(_SQL_OVERALL)
            for podcaster, sector, p, f, s, ev in c.fetchall():
                rows_to_save.append(dict(
                    podcaster=podcaster, sector=sector,
                    pass_count=p, fail_count=f, skip_count=s,
                    evaluatable=ev, accuracy=_calc_accuracy(p, ev),
                ))

            # ── 2. 分產業 ──
            c.execute(_SQL_BY_SECTOR)
            for podcaster, sector, p, f, s, ev in c.fetchall():
                rows_to_save.append(dict(
                    podcaster=podcaster, sector=sector,
                    pass_count=p, fail_count=f, skip_count=s,
                    evaluatable=ev, accuracy=_calc_accuracy(p, ev),
                ))

        self.stdout.write(f"計算完成：{len(rows_to_save)} 筆（全體 + 分產業）\n")

        # ── 印出摘要 ──
        self._print_table(rows_to_save)

        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY RUN] 不寫入資料庫"))
            return

        # ── 3. 寫入（先清空再整批 INSERT）──
        SpeakerAccuracy.objects.using("summariesdb").all().delete()

        objs = [
            SpeakerAccuracy(
                podcaster=r["podcaster"],
                sector=r["sector"],
                pass_count=r["pass_count"],
                fail_count=r["fail_count"],
                skip_count=r["skip_count"],
                evaluatable=r["evaluatable"],
                accuracy=r["accuracy"],
            )
            for r in rows_to_save
        ]
        SpeakerAccuracy.objects.using("summariesdb").bulk_create(objs)
        self.stdout.write(self.style.SUCCESS(f"\n✓ 寫入 speaker_accuracy：{len(objs)} 筆"))

    # ── 輔助：印出表格 ────────────────────────────────────────────────────────

    def _print_table(self, rows: list[dict]):
        # 先印全體
        overall = [r for r in rows if r["sector"] == ""]
        self.stdout.write(
            f"\n{'講者':<30} {'pass':>5} {'fail':>5} {'skip':>5} {'可評估':>6}  {'準確率':>7}"
        )
        self.stdout.write("─" * 72)
        for r in overall:
            acc_str = f"{r['accuracy']*100:.1f}%" if r["accuracy"] is not None else "N/A"
            bar = "█" * int((r["accuracy"] or 0) * 10) + "░" * (10 - int((r["accuracy"] or 0) * 10))
            self.stdout.write(
                f"{r['podcaster']:<30} {r['pass_count']:>5} {r['fail_count']:>5} "
                f"{r['skip_count']:>5} {r['evaluatable']:>6}  {acc_str:>7}  {bar}"
            )

        # 再印分產業（按 podcaster 分組）
        sector_rows = [r for r in rows if r["sector"] != ""]
        if not sector_rows:
            return

        self.stdout.write(f"\n\n{'分產業明細':─<72}")
        current_podcaster = None
        for r in sector_rows:
            if r["podcaster"] != current_podcaster:
                current_podcaster = r["podcaster"]
                self.stdout.write(f"\n  ▶ {current_podcaster}")
            acc_str = f"{r['accuracy']*100:.1f}%" if r["accuracy"] is not None else "N/A"
            self.stdout.write(
                f"    {r['sector']:<18} pass={r['pass_count']:>3}  fail={r['fail_count']:>3}  "
                f"可評={r['evaluatable']:>3}  {acc_str}"
            )
