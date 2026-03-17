from __future__ import annotations

import json
import os
import re
import uuid
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
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
    
    model = genai.GenerativeModel("gemini-2.5-flash")

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

        # --- 只校對現有 SRT（不重跑 Whisper） ---


def fix_existing_srt_only(
    srt_path: Path, gemini_key: str
) -> None:
    """
    當 SRT 已經存在時，只重新執行 Gemini 語意校對並覆蓋檔案
    """
    print(f"   [Smart Skip] 偵測到 SRT 已存在，跳過 Whisper 轉錄，直接校對...")

    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()

    srt_blocks = srt_content.strip().split("\n\n")
    batch_size = 50
    final_srt_blocks = []

    print(f"   [Step 2] 開始 Gemini AI 語意校對（共 {len(srt_blocks)} 區塊）...")
    for i in range(0, len(srt_blocks), batch_size):
        batch = "\n\n".join(srt_blocks[i : i + batch_size])
        print(
            f"      處理進度：校正第 {i+1} ~ {min(i + batch_size, len(srt_blocks))} 句..."
        )
        fixed_batch = fix_content_with_gemini(batch, gemini_key)
        final_srt_blocks.append(fixed_batch)

    # 覆蓋存檔
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(final_srt_blocks))

    print(f"   [✓] Gemini 校對完成，已覆蓋 {srt_path.name}")

    
    
# --- Supabase 上傳與資料庫寫入（安全版：強制產生純 ASCII 路徑） ---

def upload_to_supabase_and_insert_db(
    audio_path: Path,
    srt_path: Path,
    show_name: str,
    episode_title: str,
    supabase_url: str,
    supabase_key: str,
    bucket_name: str,
) -> str:
    """
    1. 上傳音檔到 Storage（使用安全路徑，避免中文/特殊字符）
    2. 取得公開 URL
    3. 讀取 SRT 內容
    4. 寫入 podcasts_metadata 資料表
    回傳 audio_url
    """
    try:
        from supabase import create_client, Client
    except ImportError:
        raise ImportError("請先 pip install supabase")

    supabase: Client = create_client(supabase_url, supabase_key)

    import re
    import uuid

    # 產生完全安全的路徑部分（只保留 ASCII 英數字、底線、連字號、點）
    def safe_path_part(text: str, max_len: int = 80) -> str:
        if not text:
            return f"unnamed_{uuid.uuid4().hex[:8]}"

        from pypinyin import pinyin, Style

        # 先轉成拼音（不帶聲調，方便閱讀）
        pinyin_list = pinyin(text, style=Style.NORMAL, heteronym=False)
        pinyin_text = ''.join(item[0] for item in pinyin_list)

        # 再清理不安全字符
        pinyin_text = re.sub(r'[^\w\s.-]', '_', pinyin_text)
        pinyin_text = re.sub(r'\s+', '_', pinyin_text.strip())

        # 避免太長
        result = pinyin_text[:max_len]
        return result or f"unnamed_{uuid.uuid4().hex[:8]}"

    safe_show = safe_path_part(show_name)
    safe_episode = safe_path_part(episode_title)

    # 加唯一後綴避免同名衝突（UUID 前 8 碼）
    unique_suffix = uuid.uuid4().hex[:8]
    storage_path = f"{safe_show}/{safe_episode}_{unique_suffix}{audio_path.suffix}"

    print(f"   [Supabase] 正在上傳到安全路徑：{storage_path}")

        # 上傳音檔（允許覆蓋）
    with open(audio_path, "rb") as audio_file:
        try:
            upload_res = supabase.storage.from_(bucket_name).upload(
                path=storage_path,
                file=audio_file,
                file_options={"upsert": "true"}
            )

            # debug 用（之後可移除）
            print(f"   [DEBUG] UploadResponse: {upload_res}")

            # 成功上傳：沒有拋出例外 + 回傳 UploadResponse 物件 → 就成功
            print(f"   [Supabase] 上傳成功！檔案路徑：{upload_res.full_path or upload_res.path}")

        except Exception as upload_err:
            # 只有真的失敗才會進這裡
            print(f"   [DEBUG - Real Upload Error] {repr(upload_err)}")
            raise Exception(f"上傳失敗: {str(upload_err)}")
        
    # 取得公開 URL
    audio_url = supabase.storage.from_(bucket_name).get_public_url(storage_path)

    # 讀取 SRT 完整內容
    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read().strip()

    # 插入資料庫（show_name 和 episode_title 保持原始中文，方便搜尋）
    data = {
        "show_name": show_name or "未知頻道",
        "episode_title": episode_title or audio_path.stem,
        "audio_url": audio_url,
        "srt_content": srt_content,
    }

    insert_res = supabase.table("podcasts_metadata").insert(data).execute()

    if hasattr(insert_res, "error") and insert_res.error:
        raise Exception(f"資料庫插入失敗: {insert_res.error}")

    print(f"   [Supabase] 已成功上傳音檔 & 寫入資料庫 → {audio_url}")
    return audio_url