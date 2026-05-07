import time
import logging

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import connections

from apps.podcasts.models import PodcastEpisode
from apps.summaries.models import SummaryRecord
from apps.summaries.services.engine import summarize_from_srt_text
from apps.summaries.views import _save_summary

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF = [10, 30, 60]  # seconds between retries


def summarize_with_retry(api_key, srt, mode, published_at, stdout, forced_topics=None, pro_result=None):
    for attempt in range(MAX_RETRIES):
        try:
            return summarize_from_srt_text(
                api_key=api_key,
                srt_text=srt,
                mode=mode,
                published_at=published_at,
                forced_topics=forced_topics,
                pro_result=pro_result,
            )
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[attempt]
                stdout.write(f"    → 第{attempt+1}次失敗，{wait}s 後重試：{e}")
                time.sleep(wait)
            else:
                raise


class Command(BaseCommand):
    help = "對所有有逐字稿的 podcast 集數依序產生 novice 與 pro 摘要"

    def add_arguments(self, parser):
        parser.add_argument(
            "--episode-ids",
            nargs="+",
            type=int,
            metavar="ID",
            help="只處理指定的 episode id（空白分隔），例如 --episode-ids 158 159 160",
        )

    def handle(self, *args, **options):
        import os
        api_key = getattr(settings, "GEMINI_API_KEY", "").strip()
        vertex_project = os.getenv("VERTEX_PROJECT_ID", "").strip()
        if not api_key and not vertex_project:
            self.stderr.write("GEMINI_API_KEY 或 VERTEX_PROJECT_ID 未設定，終止。")
            return

        qs = (
            PodcastEpisode.objects.using("podcasts")
            .select_related("podcast", "transcript")
            .filter(transcript__isnull=False)
            .order_by("id")
        )
        if options["episode_ids"]:
            qs = qs.filter(id__in=options["episode_ids"])

        # 取得所有有逐字稿的集數
        episodes = list(qs)

        # 已有摘要的 episode_id（novice 和 pro 都有才算完成）
        done_novice = set(
            SummaryRecord.objects.using("summariesdb")
            .filter(mode="novice", episode_id__isnull=False)
            .values_list("episode_id", flat=True)
        )
        done_pro = set(
            SummaryRecord.objects.using("summariesdb")
            .filter(mode="pro", episode_id__isnull=False)
            .values_list("episode_id", flat=True)
        )

        total = len(episodes)
        success = 0
        failed = []  # [(episode_id, title, mode, error)]

        for i, episode in enumerate(episodes, 1):
            title = episode.episode_title
            self.stdout.write(f"[{i}/{total}] {title}")

            # 每集前關閉舊連線，避免長時間 batch 造成 DB 連線逾時
            for conn in connections.all():
                conn.close_if_unusable_or_obsolete()

            transcript = getattr(episode, "transcript", None)
            if not transcript or not transcript.srt_content:
                self.stdout.write("  跳過：沒有逐字稿")
                continue

            srt = transcript.srt_content
            common = dict(
                source_filename=title,
                podcaster=episode.podcast.show_name,
                published_at=episode.published_at,
                episode_id=episode.id,
                api_key=api_key,
            )

            # pro 先跑，抽 topic 清單，再傳給 novice 確保主題一致
            pro_result = None

            # --- pro ---
            if episode.id in done_pro:
                self.stdout.write("  pro：已存在，跳過")
                # 從 DB 讀回完整 pro 供 novice 的 merge 使用
                existing_pro = (
                    SummaryRecord.objects.using("summariesdb")
                    .filter(mode="pro", episode_id=episode.id)
                    .values(
                        "arguments", "investment_takeaways",
                        "one_sentence_summary", "tags", "entities", "outlook_calls",
                    )
                    .first()
                )
                if existing_pro:
                    pro_result = {
                        "arguments": existing_pro["arguments"],
                        "investment_takeaways": existing_pro["investment_takeaways"],
                        "one_sentence_summary": existing_pro["one_sentence_summary"],
                        "tags": existing_pro["tags"],
                        "entities": existing_pro["entities"],
                        "outlook_calls": existing_pro["outlook_calls"],
                    }
                success += 1
            else:
                try:
                    pro_result = summarize_with_retry(api_key, srt, "pro", episode.published_at, self.stdout)
                    _save_summary(pro_result, mode="pro", **common)
                    self.stdout.write("  pro：完成")
                    success += 1
                    time.sleep(3)
                except Exception as e:
                    self.stdout.write(f"  pro：失敗 → {e}")
                    failed.append((episode.id, title, "pro", str(e)))

            # --- novice（只重跑 investment_takeaways + arguments，其餘從 pro 複製）---
            if episode.id in done_novice:
                self.stdout.write("  novice：已存在，跳過")
            else:
                try:
                    result = summarize_with_retry(
                        api_key, srt, "novice", episode.published_at, self.stdout,
                        pro_result=pro_result,
                    )
                    _save_summary(result, mode="novice", **common)
                    self.stdout.write("  novice：完成")
                    time.sleep(3)
                except Exception as e:
                    self.stdout.write(f"  novice：失敗 → {e}")
                    failed.append((episode.id, title, "novice", str(e)))

        # 結果摘要
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(f"完成：{success}/{total} 集全部成功")
        if failed:
            self.stdout.write(f"失敗 {len(failed)} 筆：")
            for ep_id, t, mode, err in failed:
                self.stdout.write(f"  id={ep_id} [{mode}] {t}")
                self.stdout.write(f"    原因：{err}")
