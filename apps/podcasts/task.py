from __future__ import annotations

import os
import sys
import feedparser                    
from pathlib import Path

from dotenv import load_dotenv

# 從 service 引入所有需要的函式
# 從 service 引入所有需要的函式
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
    upload_to_supabase_and_insert_db,
)


# --- 程式進入點 ---


def main():
    # 載入環境變數檔案
    load_dotenv()

    print("=== 自動化 Podcast 轉錄與 AI 校對工具 ===\n")

    # 只檢查 Gemini 金鑰
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        gemini_key = input_nonempty("請輸入 GEMINI_API_KEY: ")
    else:
        print(f"   [✓] 已從 .env 讀取 GEMINI_API_KEY")

    # ── 新增這段：讀取 Supabase 變數 ──
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    bucket_name  = os.getenv("SUPABASE_BUCKET")
    supabase_enabled = bool(supabase_url and supabase_key and bucket_name)

    if supabase_enabled:
        print(f"   [✓] 已從 .env 讀取 Supabase 設定 (Bucket: {bucket_name})")
    else:
        print("   [⚠️] .env 缺少 Supabase 設定，將僅儲存本地檔案")

    # 運行參數設定
    podcast_name = input_nonempty("請輸入 Podcast 名稱: ")
    n = input_int("處理集數 N", default=1)
    start = input_int("起始集數 (0 代表最新集)", default=0)
    language = input_optional("語言代碼 (如 zh/en，預設為自動偵測): ")
    # ... 後面原本的程式碼不變 ...

    # 設定輸出目錄（基於專案根目錄）
    script_path = Path(__file__).resolve()
    project_root = (
        script_path.parent.parent.parent
    )  # 根據 apps/podcasts/ 結構回推根目錄
    outdir = project_root / "out"
    outdir.mkdir(parents=True, exist_ok=True)

    # 執行檢索與下載
    show = itunes_search_first_podcast(podcast_name)
    feed_url = show.get("feedUrl")
    show_name = show.get("collectionName", podcast_name)
    feed = feedparser.parse(feed_url)          # ← 現在可以正常使用了
    entries = list(feed.entries or [])
    selected = entries[start : start + n]

    safe_show = sanitize_filename(show_name)
    show_dir = outdir / safe_show
    show_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[目標頻道] {show_name}")
    print(f"[輸出路徑] {show_dir}\n")

        # 批次執行轉錄 + 上傳任務
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

            # 下載音檔（如果不存在）
            if not audio_path.exists():
                print(f"下載音檔: {title}")
                download_file(url, audio_path)

            # 轉錄 + 校對（使用智慧跳過）
            if sub_path.exists():
                print(f"   [Smart Skip] SRT 已存在，直接校對...")
                fix_existing_srt_only(sub_path, gemini_key)
            else:
                transcribe_and_fix(audio_path, sub_path, language, gemini_key)

            print(f"任務成功：{sub_path.name}")

            # === 上傳到 Supabase ===
            if supabase_enabled:
                try:
                    audio_url = upload_to_supabase_and_insert_db(
                        audio_path=audio_path,
                        srt_path=sub_path,
                        show_name=show_name,
                        episode_title=title,  # 用原始標題
                        supabase_url=supabase_url,
                        supabase_key=supabase_key,
                        bucket_name=bucket_name,
                    )
                    # 可選：印出 URL 讓你確認
                except Exception as sup_err:
                    print(f"   [Supabase 錯誤] 上傳/寫入失敗（本地檔案已完成）: {sup_err}")
            else:
                print("   [Supabase 已停用] 只儲存本地")

        except Exception as e:
            print(f"任務失敗：{title} | 原因：{e}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)