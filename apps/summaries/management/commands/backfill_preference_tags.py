import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.summaries.models import SummaryRecord
from apps.summaries.services.gemini import make_client
from apps.summaries.services.preference_tags import generate_preference_tags


class Command(BaseCommand):
    help = (
        "幫最新 N 集補 primary_tags / analysis_style / investor_strategy（推薦用標籤）。"
        "「最新」跟首頁推薦一樣依 created_at 排序；同一集的 novice/pro 兩筆寫入相同結果。"
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50, help="處理最新幾集（預設 50）")
        parser.add_argument("--force", action="store_true", help="已經有標籤的集數也重跑")
        parser.add_argument("--dry-run", action="store_true", help="只印出結果，不寫入資料庫")
        parser.add_argument("--model", default="models/gemini-2.5-flash-lite")

    def handle(self, *args, **options):
        api_key = getattr(settings, "GEMINI_API_KEY", "").strip()
        if not api_key and not os.getenv("VERTEX_PROJECT_ID", "").strip():
            self.stderr.write("GEMINI_API_KEY 或 VERTEX_PROJECT_ID 未設定，終止。")
            return
        client = make_client(api_key)

        # 依 created_at 由新到舊，取前 limit 個不同的 source_filename（＝集數）
        episodes = []
        seen = set()
        for name in (SummaryRecord.objects.using("summariesdb")
                     .order_by("-created_at")
                     .values_list("source_filename", flat=True)):
            if name in seen:
                continue
            seen.add(name)
            episodes.append(name)
            if len(episodes) >= options["limit"]:
                break

        done = skipped = 0
        failed = []
        for i, name in enumerate(episodes, 1):
            records = list(SummaryRecord.objects.using("summariesdb").filter(source_filename=name))
            # 以 pro 版為準（內容較完整）；沒有 pro 就用任一筆
            base = next((r for r in records if r.mode == "pro"), records[0])

            if not options["force"] and base.primary_tags:
                skipped += 1
                continue

            summary = {
                "one_sentence_summary": base.one_sentence_summary,
                "investment_takeaways": base.investment_takeaways,
                "arguments": base.arguments,
                "tags": base.tags,
            }
            try:
                result = generate_preference_tags(client=client, model=options["model"], summary=summary)
            except Exception as e:
                failed.append((name, str(e)))
                self.stdout.write(f"[{i}/{len(episodes)}] 失敗：{name} → {e}")
                continue

            self.stdout.write(
                f"[{i}/{len(episodes)}] {name}\n"
                f"    主要標籤 {result['primary_tags']}｜分析方法 {result['analysis_style']}"
                f"｜投資策略 {result['investor_strategy']}"
            )
            if not options["dry_run"]:
                SummaryRecord.objects.using("summariesdb").filter(
                    id__in=[r.id for r in records]
                ).update(**result)
            done += 1
            time.sleep(0.5)

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(
            f"{'（dry-run，未寫入）' if options['dry_run'] else ''}"
            f"完成 {done} 集，略過（已有標籤）{skipped} 集，失敗 {len(failed)} 集"
        )
        for name, err in failed:
            self.stdout.write(f"  {name}：{err}")
