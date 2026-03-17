from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from dotenv import load_dotenv

import requests
import feedparser
from openai import OpenAI
import google.generativeai as genai

# 配置資訊與常數定義
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
REQUIRED_PY_PKGS = [
    "requests",
    "feedparser",
    "openai",
    "google-generativeai",
    "python-dotenv",
]

# --- Gemini 語意校對模組 ---


def fix_content_with_gemini(batch_text: str, gemini_key: str) -> str:
    """
    將 SRT 文本提交至 Gemini API 進行語意校對
    執行重點：同音錯字修正、財經專業術語校正、保持 SRT 格式結構。
    """
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

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
        response = model.generate_content(prompt)
        # 移除可能包含的 Markdown 語法標籤
        return response.text.replace("```srt", "").replace("```", "").strip()
    except Exception as e:
        print(f"   [Gemini Warning] 校對程序異常，保留原始文本: {e}")
        return batch_text


# --- 通用工具函式 ---


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
    2. 分段提交內容至 Gemini API 進行語意修正。
    3. 整合修正後的內容並產出最終 SRT 檔案。
    """
    import whisper
    from whisper.utils import get_writer

    # 第一階段：Whisper 本地端轉錄
    print(f"   [Step 1] 啟動 Whisper small 模型轉錄程序...")
    model = whisper.load_model("small")

    # 設置初始提示引導 AI 識別專業領域單字
    result = model.transcribe(
        str(audio_path),
        language=language,
        initial_prompt="這是一段關於財經、指數、市場分析與投資討論的繁體中文內容。",
        verbose=False,
    )

    # 產出暫時性 SRT 檔案
    temp_srt_path = out_path.with_suffix(".tmp.srt")
    writer = get_writer("srt", str(out_path.parent))
    writer(result, str(audio_path))
    os.rename(str(out_path.parent / f"{audio_path.stem}.srt"), str(temp_srt_path))

    # 第二階段：Gemini API 語意校正
    with open(temp_srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()

    srt_blocks = srt_content.strip().split("\n\n")
    batch_size = 50
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

    # 彙整並儲存最終校對結果
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(final_srt_blocks))

    if temp_srt_path.exists():
        os.remove(temp_srt_path)


# --- 程式進入點 ---


def main():
    # 載入環境變數檔案
    load_dotenv()

    print("=== 自動化 Podcast 轉錄與 AI 校對工具 ===\n")

    # 優先讀取環境變數，缺項則提示輸入
    openai_key = os.getenv("OPENAI_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")

    if not openai_key:
        openai_key = input_nonempty("請輸入 OPENAI_API_KEY: ")
    if not gemini_key:
        gemini_key = input_nonempty("請輸入 GEMINI_API_KEY: ")

    os.environ["OPENAI_API_KEY"] = openai_key

    # 運行參數設定
    podcast_name = input_nonempty("請輸入 Podcast 名稱: ")
    n = input_int("處理集數 N", default=1)
    start = input_int("起始集數 (0 代表最新集)", default=0)
    language = input_optional("語言代碼 (如 zh/en，預設為自動偵測): ")

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
    feed = feedparser.parse(feed_url)
    entries = list(feed.entries or [])
    selected = entries[start : start + n]

    safe_show = sanitize_filename(show_name)
    show_dir = outdir / safe_show
    show_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[目標頻道] {show_name}")
    print(f"[輸出路徑] {show_dir}\n")

    # 批次執行轉錄任務
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

            if not audio_path.exists():
                print(f"下載音檔: {title}")
                download_file(url, audio_path)

            transcribe_and_fix(audio_path, sub_path, language, gemini_key)
            print(f"任務成功：{sub_path.name}")

        except Exception as e:
            print(f"任務失敗：{title} | 原因：{e}")

    print("\n[任務結束] 已完成所有指定集數處理。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
