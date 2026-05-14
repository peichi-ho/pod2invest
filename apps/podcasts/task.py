from __future__ import annotations

import os
import sys
import feedparser
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv


def input_date(prompt: str) -> datetime | None:
    """讓使用者輸入日期（YYYY-MM-DD），留空代表從最新集開始"""
    while True:
        s = input(prompt).strip()
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print("格式錯誤，請輸入 YYYY-MM-DD（例如 2025-10-20）")


def parse_selection(s: str, max_index: int) -> list[int] | None:
    """
    解析使用者輸入的集數選擇，支援：
    - 單一數字：1
    - 逗號分隔：1,3,5
    - 範圍：1-3
    - 混合：1-3,5
    回傳 0-based index 列表，輸入有誤則回傳 None
    """
    indices = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                a, b = int(a), int(b)
                if a < 1 or b > max_index or a > b:
                    return None
                indices.update(range(a - 1, b))
            except ValueError:
                return None
        else:
            try:
                i = int(part)
                if i < 1 or i > max_index:
                    return None
                indices.add(i - 1)
            except ValueError:
                return None
    return sorted(indices)


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
    GeminiCorrectionError,
    insert_to_db,
)


# --- 程式進入點 ---


def main():
    print("=== 自動化 Podcast 轉錄與 AI 校對工具 ===\n")

    print("   [✓] 使用 Vertex AI 服務帳戶憑證（GOOGLE_APPLICATION_CREDENTIALS）")
    print("   [✓] 使用 Django ORM 寫入資料庫")

    podcast_name = input_nonempty("請輸入 Podcast 名稱: ")
    start_date = input_date("起始日期（YYYY-MM-DD，留空則列出最新 20 集）: ")
    display_limit = input_int("顯示集數上限", default=20, min_value=1)
    language = input_optional("語言代碼 (如 zh/en，預設為自動偵測): ")

    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent.parent
    outdir = project_root / "out"
    outdir.mkdir(parents=True, exist_ok=True)

    show = itunes_search_first_podcast(podcast_name)
    feed_url = show.get("feedUrl")
    show_name = show.get("collectionName", podcast_name)

    if not feed_url:
        raise RuntimeError(
            f"iTunes 找到「{show_name}」但沒有 RSS feed URL，請嘗試其他關鍵字。"
        )

    feed = feedparser.parse(feed_url)
    entries = list(feed.entries or [])

    def get_published(entry) -> datetime:
        if getattr(entry, "published_parsed", None):
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        return datetime.min.replace(tzinfo=timezone.utc)

    # 依日期由舊到新排序
    sorted_entries = sorted(entries, key=get_published)

    if start_date is None:
        candidates = sorted_entries[-display_limit:]
    else:
        all_filtered = [e for e in sorted_entries if get_published(e) >= start_date]
        if not all_filtered:
            print(f"找不到 {start_date.date()} 之後的集數，請調整日期。")
            return
        candidates = all_filtered[:display_limit]
        total = len(all_filtered)
        if total > display_limit:
            print(f"   [!] 共找到 {total} 集，顯示前 {display_limit} 集（可調整上限以顯示更多）")

    # 列出候選集數讓使用者選擇
    print(f"\n[目標頻道] {show_name}")
    print(f"找到以下 {len(candidates)} 集：\n")
    for i, entry in enumerate(candidates, start=1):
        date_str = get_published(entry).date()
        title = entry.get("title", f"episode_{i}")
        print(f"  [{i:2d}] {date_str}  {title}")

    print()
    while True:
        raw = input("請選擇要處理的集數（例如 1,3,5 或 1-3，直接 Enter 選第 1 集）: ").strip()
        if not raw:
            selected_indices = [0]
            break
        selected_indices = parse_selection(raw, len(candidates))
        if selected_indices is not None:
            break
        print(f"格式錯誤，請重新輸入（範圍 1～{len(candidates)}）。")

    selected = [candidates[i] for i in selected_indices]
    print(f"\n已選擇 {len(selected)} 集，開始處理...\n")

    safe_show = sanitize_filename(show_name)
    show_dir = outdir / safe_show
    show_dir.mkdir(parents=True, exist_ok=True)

    print(f"[輸出路徑] {show_dir}\n")

    correction_failed = []  # 收集校正失敗的集數

    for i, entry in enumerate(selected):
        title = entry.get("title", f"episode_{i}")
        safe_title = sanitize_filename(title)

        try:
            url = extract_enclosure_url(entry)
            audio_path = (
                show_dir
                / f"{safe_title}{os.path.splitext(url.split('?')[0])[1] or '.audio'}"
            )
            sub_path = show_dir / f"{safe_title}.srt"

            # 從 RSS feed 取得節目官方上傳時間
            published_at = get_published(entry)
            if published_at == datetime.min.replace(tzinfo=timezone.utc):
                published_at = None

            # 檢查資料庫是否已有此集逐字稿，有的話直接跳過
            try:
                from apps.podcasts.models import Podcast, PodcastEpisode
                existing_podcast = Podcast.objects.using("podcasts").filter(show_name=show_name).first()
                if existing_podcast:
                    existing_episode = PodcastEpisode.objects.using("podcasts").filter(
                        podcast=existing_podcast,
                        episode_title=title,
                    ).select_related("transcript").first()
                    if existing_episode and getattr(existing_episode, "transcript", None):
                        print(f"   [DB Skip] 資料庫已有此集逐字稿，略過：{title}")
                        continue
            except Exception as db_check_err:
                print(f"   [!] 資料庫檢查失敗（{db_check_err}），繼續處理...")

            # 下載音檔（如果不存在）
            if not audio_path.exists():
                print(f"下載音檔: {title}")
                download_file(url, audio_path)

            # 轉錄 + 校對
            try:
                if sub_path.exists():
                    print(f"   [Smart Skip] SRT 已存在，直接校對...")
                    fix_existing_srt_only(sub_path)
                else:
                    transcribe_and_fix(audio_path, sub_path, language)
            except GeminiCorrectionError as e:
                print(f"   [!] Gemini 校正失敗，略過資料庫寫入：{title}")
                correction_failed.append(title)
                continue

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

    if correction_failed:
        print(f"\n{'='*50}")
        print(f"以下 {len(correction_failed)} 集 Gemini 校正失敗，SRT 已存在本地，請用 fix_insert.py 補跑：")
        for t in correction_failed:
            print(f"  - {t}")
        print(f"{'='*50}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] 使用者中止程式")
        sys.exit(130)
    except BaseException as e:
        print(f"\n[!] 未預期錯誤（{type(e).__name__}）: {e}")
        raise
