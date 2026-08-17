"""
把 out/ 目錄下所有 SRT 直接寫入資料庫，跳過 Whisper 和 Gemini。
會先查 iTunes + RSS 補齊 audio_url 和 published_at。
用法：
  python fix_insert.py
  python fix_insert.py --show "股癌"
"""

import os
import sys
import argparse
import feedparser
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django; django.setup()

from django.db import close_old_connections
from apps.podcasts.models import Podcast, PodcastEpisode, PodcastTranscript
from apps.podcasts.service import (
    itunes_search_first_podcast,
    extract_enclosure_url,
    sanitize_filename,
)


def get_published(entry) -> datetime:
    if getattr(entry, "published_parsed", None):
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def fetch_rss_map(show_folder_name: str) -> tuple[str, dict]:
    """
    查 iTunes 取得 RSS，回傳 (show_name, {sanitized_title: entry})
    失敗時回傳 (show_folder_name, {})
    """
    try:
        show_info = itunes_search_first_podcast(show_folder_name)
        show_name = show_info.get("collectionName", show_folder_name)
        feed_url = show_info.get("feedUrl")
        if not feed_url:
            return show_name, {}
        feed = feedparser.parse(feed_url)
        rss_map = {
            sanitize_filename(e.get("title", "")): e
            for e in (feed.entries or [])
        }
        return show_name, rss_map
    except Exception as e:
        print(f"   [!] RSS 查詢失敗（{e}），audio_url 和 published_at 將留空")
        return show_folder_name, {}


def is_in_db(show_name: str, episode_title: str) -> bool:
    try:
        podcast = Podcast.objects.using("podcasts").filter(show_name=show_name).first()
        if not podcast:
            return False
        episode = PodcastEpisode.objects.using("podcasts").filter(
            podcast=podcast, episode_title=episode_title
        ).select_related("transcript").first()
        return bool(episode and getattr(episode, "transcript", None))
    except Exception:
        return False


def insert_srt(
    show_name: str,
    episode_title: str,
    srt_path: Path,
    audio_url: str = "",
    published_at=None,
) -> None:
    close_old_connections()
    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read().strip()

    podcast, _ = Podcast.objects.using("podcasts").get_or_create(show_name=show_name)
    episode, _ = PodcastEpisode.objects.using("podcasts").get_or_create(
        podcast=podcast,
        episode_title=episode_title,
        defaults={"audio_url": audio_url, "published_at": published_at},
    )
    PodcastTranscript.objects.using("podcasts").update_or_create(
        episode=episode,
        defaults={"srt_content": srt_content},
    )
    print(f"   [✓] 寫入成功：{episode_title}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", type=str, default=None, help="只處理這個節目資料夾名稱")
    args = parser.parse_args()

    outdir = BASE_DIR / "out"
    if not outdir.exists():
        print("找不到 out/ 目錄")
        sys.exit(1)

    show_dirs = [d for d in outdir.iterdir() if d.is_dir()]
    if args.show:
        show_dirs = [d for d in show_dirs if args.show in d.name]
        if not show_dirs:
            print(f"找不到符合 '{args.show}' 的節目資料夾")
            sys.exit(1)

    total_inserted = 0
    total_skipped = 0

    for show_dir in show_dirs:
        srt_files = list(show_dir.glob("*.srt"))
        if not srt_files:
            continue

        print(f"\n[節目] {show_dir.name}（查詢 RSS 中...）")
        show_name, rss_map = fetch_rss_map(show_dir.name)
        print(f"   iTunes 名稱：{show_name}，RSS 集數：{len(rss_map)}")
        print(f"   本地 SRT：{len(srt_files)} 個")

        for srt_path in srt_files:
            safe_title = srt_path.stem

            # 從 RSS map 找到對應的集數
            entry = rss_map.get(safe_title)
            if entry:
                episode_title = entry.get("title", safe_title)
                try:
                    audio_url = extract_enclosure_url(entry)
                except Exception:
                    audio_url = ""
                pub = get_published(entry)
                published_at = None if pub == datetime.min.replace(tzinfo=timezone.utc) else pub
            else:
                episode_title = safe_title
                audio_url = ""
                published_at = None

            if is_in_db(show_name, episode_title):
                print(f"   [Skip] 已在 DB：{episode_title}")
                total_skipped += 1
                continue

            try:
                insert_srt(show_name, episode_title, srt_path, audio_url, published_at)
                total_inserted += 1
            except Exception as e:
                print(f"   [!] 失敗：{episode_title} → {e}")

    print(f"\n完成：寫入 {total_inserted} 集，跳過 {total_skipped} 集")


if __name__ == "__main__":
    main()
