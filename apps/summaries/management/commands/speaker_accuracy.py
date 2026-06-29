"""
統計每位講者的回測準確率。

用法：
  python manage.py speaker_accuracy          # 印出所有講者統計
  python manage.py speaker_accuracy --csv    # 輸出 CSV 格式
  python manage.py speaker_accuracy --min 5  # 只顯示至少 5 筆可評估記錄的講者
"""
from collections import defaultdict

from django.core.management.base import BaseCommand

from apps.summaries.models import BacktestingRecord


class Command(BaseCommand):
    help = "統計每位講者的回測預測準確率"

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv", action="store_true",
            help="輸出 CSV 格式",
        )
        parser.add_argument(
            "--min", type=int, default=1, dest="min_eval",
            help="只顯示至少 N 筆可評估記錄的講者（預設 1）",
        )

    def handle(self, *args, **options):
        csv_mode = options["csv"]
        min_eval = options["min_eval"]

        # 一次撈完所有非 pending 記錄，附帶 podcaster
        qs = (
            BacktestingRecord.objects
            .using("summariesdb")
            .exclude(result="pending")
            .select_related("summary")
            .values("result", "summary__podcaster", "asset", "ticker",
                    "direction", "timeframe_raw", "start_time", "end_time")
        )

        # 按講者彙總
        stats: dict[str, dict] = defaultdict(lambda: {"pass": 0, "fail": 0, "skip": 0})

        for row in qs:
            podcaster = (row["summary__podcaster"] or "（未知）").strip()
            result = row["result"]
            stats[podcaster][result] = stats[podcaster].get(result, 0) + 1

        # 排序：可評估筆數（pass+fail）由多到少
        def sort_key(item):
            d = item[1]
            return -(d["pass"] + d["fail"])

        sorted_stats = sorted(stats.items(), key=sort_key)

        # 過濾最少筆數
        sorted_stats = [
            (p, d) for p, d in sorted_stats
            if d["pass"] + d["fail"] >= min_eval
        ]

        if csv_mode:
            self.stdout.write("講者,pass,fail,skip,可評估,準確率%")
            for podcaster, d in sorted_stats:
                total_eval = d["pass"] + d["fail"]
                acc = d["pass"] / total_eval * 100 if total_eval else 0
                self.stdout.write(
                    f"{podcaster},{d['pass']},{d['fail']},{d['skip']},"
                    f"{total_eval},{acc:.1f}"
                )
        else:
            self._print_table(sorted_stats)

        # 全體加總
        all_pass = sum(d["pass"] for _, d in stats.items())
        all_fail = sum(d["fail"] for _, d in stats.items())
        all_skip = sum(d["skip"] for _, d in stats.items())
        total_eval = all_pass + all_fail
        acc = all_pass / total_eval * 100 if total_eval else 0

        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(
            f"全體合計：pass {all_pass} / fail {all_fail} / skip {all_skip}  "
            f"→ 準確率 {acc:.1f}%（{total_eval} 筆可評估）"
        )

    def _print_table(self, sorted_stats):
        header = f"{'講者':<25} {'pass':>5} {'fail':>5} {'skip':>5} {'可評估':>6} {'準確率':>7}"
        self.stdout.write(header)
        self.stdout.write("-" * 70)

        for podcaster, d in sorted_stats:
            total_eval = d["pass"] + d["fail"]
            acc = d["pass"] / total_eval * 100 if total_eval else 0
            bar = self._bar(acc)
            self.stdout.write(
                f"{podcaster:<25} {d['pass']:>5} {d['fail']:>5} {d['skip']:>5} "
                f"{total_eval:>6}   {acc:>5.1f}%  {bar}"
            )

    @staticmethod
    def _bar(pct: float, width: int = 10) -> str:
        filled = round(pct / 100 * width)
        return "█" * filled + "░" * (width - filled)
