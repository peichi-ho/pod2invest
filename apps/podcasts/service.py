from __future__ import annotations

import os
import sys
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

# Django 初始化（讓 ORM 可以在腳本中使用）
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

import requests
from google import genai

# 配置資訊與常數定義
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"


# --- Gemini 語意校對模組 ---


def fix_content_with_gemini(batch_text: str, gemini_key: str) -> str:
    """
    將 SRT 文本提交至 Gemini API 進行語意校對
    執行重點：同音錯字修正、財經專業術語校正、保持 SRT 格式結構。
    """
    client = genai.Client(api_key=gemini_key)


    prompt = f"""
    任務：專業逐字稿校對。
    輸入內容：SRT 格式文本。
    校對重點：修正同音錯字（如：解封日、指數名稱）與財經法律專有名詞。
    規則：
    1. 嚴格保持時間戳記（00:00:00,000 --> 00:00:00,000）與序號。
    2. 僅修正文本錯誤，不變動格式。
    3. 直接輸出修正後的內容，不包含額外說明。

    待校對內容：
    {batch_text}
    """
    try:
        response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
)

        return response.text.replace("```srt", "").replace("```", "").strip()
    except Exception as e:
        print(f"   [Gemini Warning] 校對程序異常，保留原始文本: {e}")
        return batch_text


# --- 通用工具函式 ---


def format_timestamp(seconds: float) -> str:
    """將秒數轉換為 SRT 時間戳記格式"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def sanitize_filename(name: str, max_len: int = 140) -> str:
    """處理非法檔名符號，限制檔名長度"""
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:max_len] if len(name) > max_len else name


def input_nonempty(prompt: str) -> str:
    """確保使用者輸入非空字串"""
    while True:
        s = input(prompt).strip()
        if s:
            return s
        print("輸入值不可為空。")


def input_int(prompt: str, default: int, min_value: int = 0) -> int:
    """導引使用者輸入符合範圍的整數"""
    s = input(f"{prompt} (預設 {default}): ").strip()
    if not s:
        return default
    try:
        v = int(s)
        if v < min_value:
            raise ValueError
        return v
    except ValueError:
        print(f"請輸入大於或等於 {min_value} 的整數。")
        return input_int(prompt, default, min_value)


def input_optional(prompt: str) -> str | None:
    """處理可選輸入項"""
    s = input(prompt).strip()
    return s if s else None


# --- Podcast 檢索與下載邏輯 ---


def itunes_search_first_podcast(term: str, limit: int = 5) -> dict[str, Any]:
    """檢索 iTunes Podcast 資料庫並回傳首位匹配結果"""
    params = {"term": term, "media": "podcast", "entity": "podcast", "limit": limit}
    url = f"{ITUNES_SEARCH_URL}?{urlencode(params)}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        raise RuntimeError(f"查無結果：{term}")
    return results[0]


def extract_enclosure_url(entry) -> str:
    """從 RSS 項目中提取音檔下載連結"""
    if hasattr(entry, "enclosures") and entry.enclosures:
        href = getattr(entry.enclosures[0], "href", None) or entry.enclosures[0].get(
            "href"
        )
        if href:
            return href
    if hasattr(entry, "links") and entry.links:
        for link in entry.links:
            if link.get("rel") == "enclosure":
                return link["href"]
    raise RuntimeError("無法識別音檔 URL。")


def download_file(url: str, out_path: Path) -> None:
    """執行音檔流式下載並儲存至指定路徑"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


# --- 核心轉錄與 AI 校稿流程 ---


def transcribe_and_fix(
    audio_path: Path, out_path: Path, language: str | None, gemini_key: str
):
    """
    轉錄流程管理：
    1. 調用 Whisper Local 模型進行初步轉錄。
    2. 手動從 segments 產生 SRT（避免 Windows 中文檔名問題）。
    3. 分段提交內容至 Gemini API 進行語意修正。
    4. 整合修正後的內容並產出最終 SRT 檔案。
    """
    import shutil
    from faster_whisper import WhisperModel
    if not shutil.which("ffmpeg"):
        raise RuntimeError("找不到 ffmpeg，請先安裝：winget install ffmpeg")

    print(f"   [Step 1] 啟動 faster-whisper small 模型轉錄程序...")
    model = WhisperModel("small", device="cpu", compute_type="int8")

    segments, _ = model.transcribe(
        str(audio_path),
        language=language,
        initial_prompt="這是一段關於財經、指數、市場分析與投資討論的繁體中文內容。",
    )

    # 從 segments 產生 SRT
    temp_srt_path = out_path.with_suffix(".tmp.srt")
    srt_lines = []
    for i, seg in enumerate(segments, start=1):
        start = format_timestamp(seg.start)
        end = format_timestamp(seg.end)
        text = seg.text.strip()
        srt_lines.append(f"{i}\n{start} --> {end}\n{text}\n")

    with open(temp_srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))

    # 第二階段：Gemini API 語意校正
    with open(temp_srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()

    srt_blocks = srt_content.strip().split("\n\n")
    batch_size = 100
    final_srt_blocks = []

    print(
        f"   [Step 2] 轉錄完成（共 {len(srt_blocks)} 區塊），啟動 Gemini AI 語意校對..."
    )
    for i in range(0, len(srt_blocks), batch_size):
        batch = "\n\n".join(srt_blocks[i : i + batch_size])
        print(
            f"      處理進度：校正第 {i+1} ~ {min(i + batch_size, len(srt_blocks))} 句..."
        )
        fixed_batch = fix_content_with_gemini(batch, gemini_key)
        final_srt_blocks.append(fixed_batch)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(final_srt_blocks))

    if temp_srt_path.exists():
        os.remove(temp_srt_path)


# --- 只校對現有 SRT（不重跑 Whisper） ---


def fix_existing_srt_only(srt_path: Path, gemini_key: str) -> None:
    """
    當 SRT 已經存在時，只重新執行 Gemini 語意校對並覆蓋檔案
    """
    print(f"   [Smart Skip] 偵測到 SRT 已存在，跳過 Whisper 轉錄，直接校對...")

    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()

    srt_blocks = srt_content.strip().split("\n\n")
    batch_size = 100
    final_srt_blocks = []

    print(f"   [Step 2] 開始 Gemini AI 語意校對（共 {len(srt_blocks)} 區塊）...")
    for i in range(0, len(srt_blocks), batch_size):
        batch = "\n\n".join(srt_blocks[i : i + batch_size])
        print(
            f"      處理進度：校正第 {i+1} ~ {min(i + batch_size, len(srt_blocks))} 句..."
        )
        fixed_batch = fix_content_with_gemini(batch, gemini_key)
        final_srt_blocks.append(fixed_batch)

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(final_srt_blocks))

    print(f"   [✓] Gemini 校對完成，已覆蓋 {srt_path.name}")


# --- 資料庫寫入（直接使用 RSS 音檔 URL） ---


def insert_to_db(
    audio_url: str,
    srt_path: Path,
    show_name: str,
    episode_title: str,
    published_at=None,
) -> None:
    """
    透過 Django ORM 將 Podcast 資料寫入資料庫。
    採正規化三表結構：
      - Podcast        → 頻道（相同頻道只建一筆）
      - PodcastEpisode → 單集 metadata（含官方上傳時間）
      - PodcastTranscript → 逐字稿（重跑校對時更新 srt_content）
    """
    from apps.podcasts.models import Podcast, PodcastEpisode, PodcastTranscript

    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read().strip()

    # 取得或建立頻道
    podcast, _ = Podcast.objects.get_or_create(
        show_name=show_name or "未知頻道",
    )

    # 取得或建立單集
    episode, _ = PodcastEpisode.objects.get_or_create(
        podcast=podcast,
        episode_title=episode_title or srt_path.stem,
        defaults={
            "audio_url": audio_url,
            "published_at": published_at,
        },
    )

    # 建立或更新逐字稿（重跑校對時覆蓋 srt_content）
    PodcastTranscript.objects.update_or_create(
        episode=episode,
        defaults={"srt_content": srt_content},
    )

    print(f"   [✓] 資料庫寫入成功 → {audio_url}")