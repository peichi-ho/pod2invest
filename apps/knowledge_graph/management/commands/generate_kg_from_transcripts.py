import sys

from django.core.management.base import BaseCommand

from apps.knowledge_graph.generate_by_podcast import process_all_episodes, process_new_episode


class Command(BaseCommand):
    """
    以逐字稿為來源批次生成知識圖譜（generate_by_podcast.py）。

    設計給大量資料、長時間跑的情境用：process_all_episodes 內部本來就會
    跳過已處理過的集數，_call_gemini_with_retry（generate.py）遇到 429
    配額耗盡會自動睡過冷卻期最長約 2.4 小時再繼續，不需要人在旁邊等配額
    恢復後手動重新執行。用 nohup / Windows 工作排程器丟到背景跑即可；
    中途真的中斷了，直接重新執行同一個指令就會從沒處理過的集數接著跑，
    不會重複打 LLM 或寫入重複資料。

    範例：
        python manage.py generate_kg_from_transcripts
        python manage.py generate_kg_from_transcripts --limit 20
        python manage.py generate_kg_from_transcripts --published-after 2026-01-01
        python manage.py generate_kg_from_transcripts --episode-ids 158 159
    """

    help = "以逐字稿為來源批次生成知識圖譜，可長時間背景執行（自動處理 429 配額冷卻）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--episode-ids",
            nargs="+",
            type=int,
            metavar="ID",
            help="只處理指定的 episode id（空白分隔），會逐一呼叫 process_new_episode，不受已處理跳過邏輯限制",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="限制本次處理的集數，供小範圍測試",
        )
        parser.add_argument(
            "--published-after",
            type=str,
            default=None,
            metavar="YYYY-MM-DD",
            help="只處理 published_at 在此日期(含)之後的集數",
        )
        parser.add_argument(
            "--published-before",
            type=str,
            default=None,
            metavar="YYYY-MM-DD",
            help="只處理 published_at 在此日期(含)之前的集數",
        )

    def handle(self, *args, **options):
        # generate.py / generate_by_podcast.py 的 print() 裡有 emoji（📚✅⏭️...）。
        # 輸出被導到檔案（例如 Start-Process -RedirectStandardOutput）時，Python
        # 會用當下終端機環境的預設編碼寫檔；在沒特別設定過 UTF-8 的 Windows
        # 中文版環境下那是 cp950（Big5），編不了 emoji 就直接讓整個程式當掉、
        # 一集都還沒處理就死了。在這裡強制 stdout/stderr 用 UTF-8（遇到真的
        # 編不了的字元用 replace，不讓輸出問題中斷批次），確保不管在哪個
        # 終端機環境執行都不會因為這個原因中斷。
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

        episode_ids = options.get("episode_ids")

        if episode_ids:
            for episode_id in episode_ids:
                self.stdout.write(f"\n🚀 處理單集 #{episode_id}")
                try:
                    process_new_episode(episode_id)
                except Exception as e:
                    self.stderr.write(f"❌ 單集 #{episode_id} 處理失敗：{e}")
            return

        process_all_episodes(
            limit=options.get("limit"),
            published_after=options.get("published_after"),
            published_before=options.get("published_before"),
        )
