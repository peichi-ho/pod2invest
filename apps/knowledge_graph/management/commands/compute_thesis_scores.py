import sys

from django.core.management.base import BaseCommand

from apps.knowledge_graph.services.thesis_score import process_backtesting_records


class Command(BaseCommand):
    """
    批次計算 BacktestingRecord 的知識圖譜信心分數（thesis_score.py）。

    跟 generate_kg_from_transcripts 一樣是設計給大量資料、長時間跑的情境：
    _is_similar_reason 內部改呼叫 generate.py 的 _call_gemini_with_retry，
    429 配額耗盡會自動睡過冷卻期（最長約 2.4 小時）再繼續，不需要人在旁邊
    等配額恢復後手動重新執行。process_backtesting_records 本身預設
    skip_existing=True，已經算過分的紀錄會跳過，所以中途真的中斷了，直接
    重新執行同一個指令就會從沒算過分的紀錄接著跑，不會重複寫入。

    範例：
        python manage.py compute_thesis_scores
        python manage.py compute_thesis_scores --limit 50
        python manage.py compute_thesis_scores --recompute
    """

    help = "批次計算知識圖譜信心分數，可長時間背景執行（自動處理 429 配額冷卻）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="限制本次處理的筆數，供小範圍測試",
        )
        parser.add_argument(
            "--recompute",
            action="store_true",
            help="不跳過已經算過分的紀錄，全部重新計算（預設只算還沒算過的）",
        )

    def handle(self, *args, **options):
        # 跟 generate_kg_from_transcripts 相同的理由：這裡的 print() 有
        # ⚠️/❌ 這類符號，輸出被導到檔案、又剛好在沒設定 UTF-8 的 Windows
        # 中文版環境執行時，預設編碼（cp950）編不了這些字元會直接讓程式
        # 當掉、一筆都還沒算就死了。強制用 UTF-8（編不了的字元用 replace）
        # 確保不管在哪個終端機環境執行都不會因為這個原因中斷。
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

        summary = process_backtesting_records(
            limit=options.get("limit"),
            skip_existing=not options.get("recompute"),
        )
        self.stdout.write(
            f"完成：處理 {summary['processed']} 筆、跳過 {summary['skipped']} 筆、"
            f"失敗 {summary['failed']} 筆（共 {summary['total']} 筆）。"
            f"知識圖譜目前處理到 {summary.get('kg_frontier')}，"
            f"另有 {summary.get('not_ready', 0)} 筆超出範圍暫不處理。"
        )
