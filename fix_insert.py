"""
直接把 out/ 裡已存在的 SRT 檔案寫入資料庫，不重跑 Whisper 或 Gemini。
用法：修改下方 show_name 和 ep_keyword 後執行。
"""
import os, sys, feedparser
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(r"C:\Users\ch\Desktop\POD2INVEST\.env"))
sys.path.insert(0, r"C:\Users\ch\Desktop\POD2INVEST")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django; django.setup()

from apps.podcasts.service import (
    itunes_search_first_podcast, sanitize_filename,
    insert_to_db, extract_enclosure_url
)

show_name  = input("節目名稱: ").strip()
ep_keyword = input("集數關鍵字（用來搜尋檔名和 RSS）: ").strip()

# 1. 在 out/ 找到對應的 srt 檔
safe_show = sanitize_filename(show_name)
show_dir  = Path(r"C:\Users\ch\Desktop\POD2INVEST\out") / safe_show
matches   = list(show_dir.glob(f"*{ep_keyword}*.srt"))

if not matches:
    print(f"找不到包含 '{ep_keyword}' 的 srt 檔，請確認 out/{safe_show}/ 資料夾")
    sys.exit(1)
if len(matches) > 1:
    print("找到多個符合的檔案，請縮小 ep_keyword：")
    for m in matches: print(f"  {m.name}")
    sys.exit(1)

srt_path = matches[0]
print(f"找到 SRT：{srt_path.name}")

# 2. 從 RSS 找音檔 URL 和完整集名
show_info = itunes_search_first_podcast(show_name)
feed      = feedparser.parse(show_info["feedUrl"])

audio_url    = None
ep_title     = None
published_at = None
for entry in feed.entries:
    if ep_keyword.lower() in entry.get("title", "").lower():
        ep_title = entry.get("title")
        if getattr(entry, "published_parsed", None):
            published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        try:
            audio_url = extract_enclosure_url(entry)
        except:
            pass
        break

if not audio_url:
    print(f"在 RSS 找不到含 '{ep_keyword}' 的集數，請手動確認節目名稱")
    sys.exit(1)

print(f"集名：{ep_title}")
print(f"音檔 URL：{audio_url}")
print(f"官方上傳時間：{published_at}")

# 3. 寫入資料庫
insert_to_db(
    audio_url=audio_url,
    srt_path=srt_path,
    show_name=show_name,
    episode_title=ep_title,
    published_at=published_at,
)
print("✓ 寫入資料庫成功")
