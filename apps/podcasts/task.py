from __future__ import annotations

import os
import sys
import feedparser
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from service import (
    input_nonempty,
    input_int,
    input_optional,
    sanitize_filename,
    itunes_search_first_podcast,
    extract_enclosure_url,
    download_file,
    transcribe_and_fix,
    fix_existing_srt_only,
    insert_to_db,
)


# --- 程式進入點 ---


def main():
    print("=== 自動化 Podcast 轉錄與 AI 校對工具 ===\n")

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        gemini_key = input_nonempty("請輸入 GEMINI_API_KEY: ")
    else:
        print(f"   [✓] 已從 .env 讀取 GEMINI_API_KEY")

    print("   [✓] 使用 Django ORM 寫入資料庫")

    podcast_name = input_nonempty("請輸入 Podcast 名稱: ")
    n = input_int("處理集數 N", default=1)
    start = input_int("起始集數 (0 代表最新集)", default=0)
    language = input_optional("語言代碼 (如 zh/en，預設為自動偵測): ")

    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent.parent
    outdir = project_root / "out"
    outdir.mkdir(parents=True, exist_ok=True)

    show = itunes_search_first_podcast(podcast_name)
    feed_url = show.get("feedUrl")
    show_name = show.get("collectionName", podcast_name)
    feed = feedparser.parse(feed_url)
    entries = list(feed.entries or [])
    selected = entries[start : start + n]

    safe_show = sanitize_filename(show_name)
    show_dir = outdir / safe_show
    show_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[目標頻道] {show_name}")
    print(f"[輸出路徑] {show_dir}\n")

    for i, entry in enumerate(selected):
        ep_index = start + i
        title = entry.get("title", f"episode_{ep_index}")
        safe_title = sanitize_filename(title)

        try:
            url = extract_enclosure_url(entry)
            audio_path = (
                show_dir
                / f"{safe_title}{os.path.splitext(url.split('?')[0])[1] or '.audio'}"
            )
            sub_path = show_dir / f"{safe_title}.srt"

            # 從 RSS feed 取得節目官方上傳時間
            published_at = None
            if getattr(entry, "published_parsed", None):
                published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

            # 下載音檔（如果不存在）
            if not audio_path.exists():
                print(f"下載音檔: {title}")
                download_file(url, audio_path)

            # 轉錄 + 校對
            if sub_path.exists():
                print(f"   [Smart Skip] SRT 已存在，直接校對...")
                fix_existing_srt_only(sub_path, gemini_key)
            else:
                transcribe_and_fix(audio_path, sub_path, language, gemini_key)

            print(f"任務成功：{sub_path.name}")

            # 寫入資料庫（使用 RSS 原始音檔 URL）
            try:
                insert_to_db(
                    audio_url=url,
                    srt_path=sub_path,
                    show_name=show_name,
                    episode_title=title,
                    published_at=published_at,
                )
            except Exception as db_err:
                print(f"   [資料庫錯誤] {db_err}")

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"任務失敗：{title} | 原因：{e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)