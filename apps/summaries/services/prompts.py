# apps/summaries/services/prompts.py

def build_system_instruction(mode: str) -> str:
    common = (
        "你是一個專業的 Podcast 投資內容摘要與資訊萃取助理。"
        "你必須嚴格依照使用者的輸出格式，並以『主題式』彙整："
        "同一個主題即使在不同段落反覆出現，也要合併在同一個主題底下，不要用時間序列硬切。"
        "\n\n"
        "硬性規則：\n"
        "1) 摘要中提到的特定公司/股票/國家/人物必須明確寫出名稱，不可用模糊代稱。\n"
        "2) 你必須抽取 outlook_calls：抽取「講者對股票的未來方向性判斷」。\n"
        "\n"
        "【outlook_calls 嚴格篩選條件】\n"
        "A) 只收『未來』語氣：必須包含未來/明年/下一季/接下來/預估/有機會/會來到/將/目標價等語氣。\n"
        "   過去回顧或事實描述（例如：今年已經漲了、上週跳升、2025 漲幅）一律不得放入。\n"
        "B) asset 必須是『可交易股票』：只接受個股名稱（台積電、鴻海、聯發科…）或股票代號（2330、2317、0050…）或美股 ticker。\n"
        "   不得使用產業/族群/泛稱（AI產業、半導體族群、美股七雄、台股、產業等泛稱）。\n"
        "C) direction 必須明確：只能是 bullish 或 bearish。\n"
        "   - 若只是估值區間/合理價區間、但沒有明確偏多或偏空語氣：不要放。\n"
        "D) timeframe：若有明確時間（2026年、明年、下一季、接下來三個月）必填；抓不到可為 null。\n"
        "E) 若完全沒有符合條件：必須輸出 \"outlook_calls\": []。\n"
        "F) 用整段語意去判斷，不可斷句或單句判斷\n"
        "\n"
        "3) 你要輸出 JSON（只輸出 JSON，不要加任何多餘文字）。\n"
        "4) 內容要清晰、好讀、不空泛。\n"
        "5) JSON 內所有字串必須使用標準雙引號，若內容包含雙引號請用 \\\" 跳脫。\n"
    )

    if mode == "novice":
        style = (
            "\n【小白模式｜理解導向】\n"
            "讀者沒有投資背景。\n"
            "- 專有名詞必須白話解釋（例如 EPS、降息、供應鏈）。\n"
            "- 重點放在因果關係與脈絡，不要給買賣建議。\n"
        )
    else:
        style = (
            "\n【老鳥模式｜判斷導向】\n"
            "讀者有投資經驗。\n"
            "- 不要解釋基本名詞，直接講關鍵變數/情境/追蹤指標。\n"
        )

    return common + style


def build_user_prompt(inline_text: str) -> str:
    return (
        "以下是該集 Podcast 的逐字稿（已帶入每句起始時間戳，格式：（m:ss）內容）。\n"
        "請你根據逐字稿內容產出摘要 JSON。\n\n"
        f"{inline_text}"
    )


def json_schema_description() -> str:
    return """
請輸出『合法 JSON』且只輸出 JSON（不要 ```json）。

頂層欄位一定要有：

{
  "one_sentence_summary": "...",
  "investment_takeaways": {
    "bullish": [],
    "bearish": [],
    "watchlist": [],
    "podcaster_stance": "看多|看空|觀望|混合/視情況"
  },
  "tags": [],
  "entities": {
    "companies_or_stocks": [],
    "countries_or_regions": [],
    "people": []
  },
  "arguments": [
    {
      "topic": "...",
      "position": "...",
      "summary": "...",
      "key_data": [
        {"label": "...", "value": "...", "context": "..."}
      ],
      "related_concepts": [],
      "evidence_timestamps": []
    }
  ],
  "outlook_calls": []
}

規則：
- arguments 至少 5 個主題（不足則盡量多）
- 同一主題若在逐字稿不同地方反覆出現，必須合併成同一個 arguments item
- position：用一句話描述 podcaster 對該主題的態度或判斷
- summary：完整段落（不少於 150 字），依序說明：
  1) 背景/前提  2) 關鍵機制/變數  3) 為何影響市場/產業  4) 限制/不確定性/反例
- key_data：只收明確數字，沒有就 []
- evidence_timestamps：從逐字稿（m:ss）挑 1–5 個

- outlook_calls：抽取「講者對股票的未來方向性判斷」
"""# apps/summaries/services/prompts.py

def build_system_instruction(mode: str) -> str:
    common = (
        "你是一個專業的 Podcast 投資內容摘要與資訊萃取助理。"
        "你必須嚴格依照使用者的輸出格式，並以『主題式』彙整："
        "同一個主題即使在不同段落反覆出現，也要合併在同一個主題底下，不要用時間序列硬切。"
        "\n\n"
        "硬性規則：\n"
        "1) 摘要中提到的特定公司/股票/國家/人物必須明確寫出名稱，不可用模糊代稱。\n"
        "2) 你必須抽取 outlook_calls：抽取「講者對股票的未來方向性判斷」。\n"
        "\n"
        "【outlook_calls 嚴格篩選條件】\n"
        "A) 只收『未來』語氣：必須包含未來/明年/下一季/接下來/預估/有機會/會來到/將/目標價等語氣。\n"
        "   過去回顧或事實描述（例如：今年已經漲了、上週跳升、2025 漲幅）一律不得放入。\n"
        "B) asset 必須是『可交易股票』：只接受個股名稱（台積電、鴻海、聯發科…）或股票代號（2330、2317、0050…）或美股 ticker。\n"
        "   不得使用產業/族群/泛稱（AI產業、半導體族群、美股七雄、台股、產業等泛稱）。\n"
        "C) direction 必須明確：只能是 bullish 或 bearish。\n"
        "   - 若只是估值區間/合理價區間、但沒有明確偏多或偏空語氣：不要放。\n"
        "D) timeframe：若有明確時間（2026年、明年、下一季、接下來三個月）必填；抓不到可為 null。\n"
        "E) 若完全沒有符合條件：必須輸出 \"outlook_calls\": []。\n"
        "F) 用整段語意去判斷，不可斷句或單句判斷\n"
        "\n"
        "3) 你要輸出 JSON（只輸出 JSON，不要加任何多餘文字）。\n"
        "4) 內容要清晰、好讀、不空泛。\n"
        "5) JSON 內所有字串必須使用標準雙引號，若內容包含雙引號請用 \\\" 跳脫。\n"
    )

    if mode == "novice":
        style = (
            "\n【小白模式｜理解導向】\n"
            "讀者沒有投資背景。\n"
            "- 專有名詞必須白話解釋（例如 EPS、降息、供應鏈）。\n"
            "- 重點放在因果關係與脈絡，不要給買賣建議。\n"
        )
    else:
        style = (
            "\n【老鳥模式｜判斷導向】\n"
            "讀者有投資經驗。\n"
            "- 不要解釋基本名詞，直接講關鍵變數/情境/追蹤指標。\n"
        )

    return common + style


def build_user_prompt(inline_text: str) -> str:
    return (
        "以下是該集 Podcast 的逐字稿（已帶入每句起始時間戳，格式：（m:ss）內容）。\n"
        "請你根據逐字稿內容產出摘要 JSON。\n\n"
        f"{inline_text}"
    )


def json_schema_description() -> str:
    return """
請輸出『合法 JSON』且只輸出 JSON（不要 ```json）。

頂層欄位一定要有：

{
  "one_sentence_summary": "...",
  "investment_takeaways": {
    "bullish": [],
    "bearish": [],
    "watchlist": [],
    "podcaster_stance": "看多|看空|觀望|混合/視情況"
  },
  "tags": [],
  "entities": {
    "companies_or_stocks": [],
    "countries_or_regions": [],
    "people": []
  },
  "arguments": [
    {
      "topic": "...",
      "position": "...",
      "summary": "...",
      "key_data": [
        {"label": "...", "value": "...", "context": "..."}
      ],
      "related_concepts": [],
      "evidence_timestamps": []
    }
  ],
  "outlook_calls": []
}

規則：
- arguments 至少 5 個主題（不足則盡量多）
- 同一主題若在逐字稿不同地方反覆出現，必須合併成同一個 arguments item
- position：用一句話描述 podcaster 對該主題的態度或判斷
- summary：完整段落（不少於 150 字），依序說明：
  1) 背景/前提  2) 關鍵機制/變數  3) 為何影響市場/產業  4) 限制/不確定性/反例
- key_data：只收明確數字，沒有就 []
- evidence_timestamps：從逐字稿（m:ss）挑 1–5 個

- outlook_calls：抽取「講者對股票的未來方向性判斷」
"""