"""
input:
  python auto_task.py                          # 每個節目抓最新 5 集
  python auto_task.py --start-date 2025-04-01  # 從指定日期開始抓
"""

import os
import sys
import json
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

from apps.podcasts.service import (
    sanitize_filename,
    itunes_search_first_podcast,
    extract_enclosure_url,
    download_file,
    transcribe_and_fix,
    fix_existing_srt_only,
    insert_to_db,
    GeminiCorrectionError,
)

DEFAULT_LIMIT = 5  # 未指定日期時，每個節目預設抓最新幾集


def notify(message: str) -> None:
    """發送 Discord 通知，失敗不影響主流程"""
    import requests as req
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        return
    try:
        req.post(webhook_url, json={"content": message}, timeout=10)
    except Exception:
        pass


def get_published(entry) -> datetime:
    if getattr(entry, "published_parsed", None):
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def is_in_db(show_name: str, episode_title: str) -> bool:
    """檢查資料庫是否已有此集逐字稿"""
    try:
        from apps.podcasts.models import Podcast, PodcastEpisode
        podcast = Podcast.objects.using("podcasts").filter(show_name=show_name).first()
        if not podcast:
            return False
        episode = PodcastEpisode.objects.using("podcasts").filter(
            podcast=podcast,
            episode_title=episode_title,
        ).select_related("transcript").first()
        return bool(episode and getattr(episode, "transcript", None))
    except Exception:
        return False


def matches_filter(title: str, filters: list, excludes: list) -> bool:
    """filter 是空的就全抓，有值才比對關鍵字；exclude 有符合就排除"""
    if any(e in title for e in excludes):
        return False
    if not filters:
        return True
    return any(f in title for f in filters)


def process_show(show_cfg: dict, start_date: datetime | None, outdir: Path) -> dict:
    name = show_cfg["name"]
    language = show_cfg.get("language", "zh")
    filters = show_cfg.get("filter", [])
    result = {"show": name, "success": [], "skipped": [], "failed": []}

    print(f"\n{'='*60}")
    print(f"[節目] {name}")

    try:
        show_info = itunes_search_first_podcast(name)
    except Exception as e:
        print(f"   [!] iTunes 查詢失敗：{e}")
        result["failed"].append(f"iTunes 查詢失敗：{e}")
        return result

    feed_url = show_info.get("feedUrl")
    show_name = show_info.get("collectionName", name)
    # Apple Podcasts 頭貼：iTunes Search API 回傳的節目資料裡就有，不用另外查
    image_url = show_info.get("artworkUrl600") or show_info.get("artworkUrl100", "")

    if not feed_url:
        print(f"   [!] 找不到 RSS feed")
        result["failed"].append("找不到 RSS feed")
        return result

    feed = feedparser.parse(feed_url)
    entries = sorted(list(feed.entries or []), key=get_published)

    if start_date:
        candidates = [e for e in entries if get_published(e) >= start_date]
    else:
        candidates = entries[-DEFAULT_LIMIT:]

    excludes = show_cfg.get("exclude", [])
    candidates = [e for e in candidates if matches_filter(e.get("title", ""), filters, excludes)]

    if not candidates:
        print(f"   沒有符合條件的集數")
        return result

    print(f"   找到 {len(candidates)} 集符合條件")

    show_dir = outdir / sanitize_filename(show_name)
    show_dir.mkdir(parents=True, exist_ok=True)

    for entry in candidates:
        title = entry.get("title", "unknown")
        safe_title = sanitize_filename(title)
        print(f"\n   處理：{title}")

        if is_in_db(show_name, title):
            print(f"   [DB Skip] 資料庫已有，略過")
            result["skipped"].append(title)
            continue

        try:
            url = extract_enclosure_url(entry)
            ext = os.path.splitext(url.split("?")[0])[1] or ".audio"
            audio_path = show_dir / f"{safe_title}{ext}"
            sub_path = show_dir / f"{safe_title}.srt"

            published_at = get_published(entry)
            if published_at == datetime.min.replace(tzinfo=timezone.utc):
                published_at = None

            if not audio_path.exists():
                print(f"   下載音檔...")
                download_file(url, audio_path)

            try:
                if sub_path.exists():
                    print(f"   [Smart Skip] SRT 已存在，直接校對...")
                    fix_existing_srt_only(sub_path)
                else:
                    transcribe_and_fix(audio_path, sub_path, language)
            except GeminiCorrectionError:
                print(f"   [!] Gemini 校正失敗，略過資料庫寫入")
                result["failed"].append(title)
                notify(f"❌ [{name}] {title} 處理失敗（Gemini 校正失敗）")
                continue

            insert_to_db(
                audio_url=url,
                srt_path=sub_path,
                show_name=show_name,
                episode_title=title,
                published_at=published_at,
                image_url=image_url,
            )
            print(f"   [✓] 完成")
            result["success"].append(title)
            notify(f"✅ [{name}] {title} 處理完成")

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"   [!] 失敗：{e}")
            result["failed"].append(title)
            notify(f"❌ [{name}] {title} 處理失敗（{e}）")

    return result


def main():
    parser = argparse.ArgumentParser(description="自動化 Podcast 批次處理")
    parser.add_argument("--start-date", type=str, help="起始日期 YYYY-MM-DD")
    args = parser.parse_args()

    start_date = None
    if args.start_date:
        try:
            start_date = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print("日期格式錯誤，請使用 YYYY-MM-DD")
            sys.exit(1)

    watchlist_path = BASE_DIR / "watchlist.json"
    if not watchlist_path.exists():
        print("找不到 watchlist.json")
        sys.exit(1)

    with open(watchlist_path, "r", encoding="utf-8") as f:
        watchlist = json.load(f)

    outdir = BASE_DIR / "out"
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"=== 自動化 Podcast 批次處理 ===")
    if start_date:
        print(f"起始日期：{start_date.date()}")
    else:
        print(f"模式：每個節目最新 {DEFAULT_LIMIT} 集")
    print(f"節目數量：{len(watchlist)}")

    all_results = []
    for show_cfg in watchlist:
        result = process_show(show_cfg, start_date, outdir)
        all_results.append(result)

    # 執行摘要
    total_success = sum(len(r["success"]) for r in all_results)
    total_skipped = sum(len(r["skipped"]) for r in all_results)
    total_failed  = sum(len(r["failed"])  for r in all_results)

    print(f"\n{'='*60}")
    print(f"=== 執行完成 ===")
    print(f"成功：{total_success} 集  |  已跳過：{total_skipped} 集  |  失敗：{total_failed} 集")

    # Discord 統計摘要
    summary_lines = [
        f"📊 **執行完成**",
        f"成功：{total_success} 集　|　跳過：{total_skipped} 集　|　失敗：{total_failed} 集",
    ]
    if total_failed > 0:
        print(f"\n失敗的集數：")
        summary_lines.append("\n**失敗的集數：**")
        for r in all_results:
            for t in r["failed"]:
                print(f"  - [{r['show']}] {t}")
                summary_lines.append(f"- [{r['show']}] {t}")

    notify("\n".join(summary_lines))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] 使用者中止程式")
        sys.exit(130)
