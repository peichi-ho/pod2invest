"""
management command：把還沒有頭貼的既有節目（Podcast.image_url 是空的），
用 iTunes Search API 查一次，補上 Apple Podcasts 上實際的封面圖。

新集數透過 auto_task.py 正常收錄時已經會自動帶入（見 auto_task.py 的
process_show()），這支 command 只補「舊資料」。

用法：
  python manage.py backfill_podcast_images                # 全部補
  python manage.py backfill_podcast_images --dry-run       # 只印不寫入
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.podcasts.models import Podcast
from apps.podcasts.service import itunes_search_first_podcast


class Command(BaseCommand):
    help = "補建既有節目的 Apple Podcasts 頭貼（Podcast.image_url）"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="只印出會查詢/更新什麼，不寫入")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        # 既有資料的 image_url 欄位存的是 NULL，不是空字串——Django 對 nullable 欄位的
        # filter()/exclude() 不會自動比對 NULL，兩種情況都要抓才不會漏掉舊資料。
        podcasts = Podcast.objects.using("podcasts").filter(
            Q(image_url="") | Q(image_url__isnull=True)
        )

        total = podcasts.count()
        updated = 0
        not_found = 0
        failed = 0

        self.stdout.write(f"找到 {total} 個節目沒有頭貼")

        for podcast in podcasts:
            try:
                show_info = itunes_search_first_podcast(podcast.show_name)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  [查無結果] {podcast.show_name}：{e}"))
                not_found += 1
                continue

            image_url = show_info.get("artworkUrl600") or show_info.get("artworkUrl100", "")
            if not image_url:
                self.stdout.write(self.style.WARNING(f"  [無頭貼欄位] {podcast.show_name}"))
                not_found += 1
                continue

            self.stdout.write(f"  {podcast.show_name} -> {image_url}" + (" [dry-run]" if dry_run else ""))
            if dry_run:
                continue

            try:
                podcast.image_url = image_url
                podcast.save(using="podcasts")
                updated += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"    [寫入失敗，跳過] {podcast.show_name}：{e}"))
                failed += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n完成：共 {total} 個節目，"
            f"{'預計' if dry_run else ''}補上 {updated} 個頭貼，"
            f"{not_found} 個查無結果，{failed} 個寫入失敗"
        ))
