# apps/summaries/services/prompts.py
from .tag_taxonomy import classification_rules_text


def build_system_instruction(mode: str) -> str:
    common = (
        "你是一個專業的 Podcast 投資內容摘要與資訊萃取助理。"
        "你必須嚴格依照使用者的輸出格式，並以『主題式』彙整："
        "同一個主題即使在不同段落反覆出現，也要合併在同一個主題底下，不要用時間序列硬切。"
        "\n\n"
        "硬性規則：\n"
        "1) 摘要中提到的特定公司/股票/國家/人物必須明確寫出名稱，不可用模糊代稱。\n"
        "2) 你要輸出 JSON（只輸出 JSON，不要加任何多餘文字）。\n"
        "3) 內容要清晰、好讀、不空泛。\n"
        "4) JSON 內所有字串必須使用標準雙引號，若內容包含雙引號請用 \\\" 跳脫。\n"
        "5) classification 各欄位必須嚴格從白名單中選，不得自創新值。\n"
        "6) 若無法確定分類，請輸出空陣列，不要猜測。\n\n"
        f"{classification_rules_text()}\n"
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


def build_novice_content_schema(forced_topics: list, pro_takeaways: dict = None) -> str:
    """
    只要求 LLM 輸出 investment_takeaways + arguments（小白風格）。
    其餘欄位（one_sentence_summary, entities, tags, outlook_calls）
    由呼叫端從 pro 複製，不在此生成。

    pro_takeaways：pro 版的 investment_takeaways dict，若提供則要求 LLM
    保持相同標的與分類，只改寫成白話說明。
    """
    topics_list = "\n".join(f"  - 「{t}」" for t in forced_topics)

    if pro_takeaways:
        import json as _json
        bullish_items = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(pro_takeaways.get("bullish") or []))
        bearish_items = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(pro_takeaways.get("bearish") or []))
        watchlist_items = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(pro_takeaways.get("watchlist") or []))
        stance = pro_takeaways.get("podcaster_stance", "混合/視情況")

        takeaways_block = f"""【investment_takeaways 鎖定規則】
以下是 pro 版的 investment_takeaways，你必須：
1. 保持完全相同的標的數量與 bullish/bearish/watchlist 分類（不可增加、刪除任何一條）
2. podcaster_stance 維持：「{stance}」
3. 每條的「標的名稱」部分不可改變，只將「—」後面的理由改寫為白話說明

Pro bullish 清單（共 {len(pro_takeaways.get('bullish') or [])} 條）：
{bullish_items or '（無）'}

Pro bearish 清單（共 {len(pro_takeaways.get('bearish') or [])} 條）：
{bearish_items or '（無）'}

Pro watchlist 清單（共 {len(pro_takeaways.get('watchlist') or [])} 條）：
{watchlist_items or '（無）'}

改寫示例：
  Pro 版：「台積電 — CoWoS 良率數據確認後加碼，目標 Q4 法說前」
  Novice 版：「台積電 — 做 AI 晶片封裝的台積電訂單超旺，專家說等一個技術數據確認後就是好時機」"""
    else:
        takeaways_block = """investment_takeaways 格式：「[具體標的] — [一句話關鍵理由]」
  ✓ 「台積電 — CoWoS 訂單滿載，白話說是做 AI 晶片的封裝訂單全滿，需求超出預期」
  ✗ 「市場整體看好」（太模糊，沒有具體標的）"""

    return f"""【最高優先規則：主題清單鎖定】
arguments 陣列必須嚴格且完整地包含以下所有主題，不可增加、刪除或修改任何字元（包括冒號、空格、括號）：
{topics_list}
每一個 topic 字串必須與上方清單完全一致，連標點符號都不能改。
違反此規則（自行增加主題、改變名稱、缺漏主題）視為格式錯誤。

{takeaways_block}

只輸出 JSON（不要 ```json 或任何說明），格式如下：

{{
  "investment_takeaways": {{
    "bullish": ["[標的] — [白話說明為什麼看漲，讓初學者能理解]"],
    "bearish": ["[標的] — [白話說明風險，讓初學者能理解]"],
    "watchlist": ["[標的] — [白話說明值得關注的原因]"],
    "podcaster_stance": "{stance if pro_takeaways else '看多|看空|觀望|混合/視情況'}"
  }},
  "arguments": [
    {{
      "topic": "（必須與上方主題清單完全一致）",
      "position": "說清楚 podcaster 的方向和原因，帶脈絡讓新手知道「為什麼」。✓「看好台積電，因為 AI 晶片需求持續拉動先進封裝訂單，產能已排到明年」✗「看多」（太短）",
      "summary": "完整段落（150–200 字），用說故事的方式依序寫：1) 這個主題的背景是什麼（白話說清楚，專有名詞要解釋）2) 發生了什麼事、為什麼普通人要在意 3) 對一般投資人代表什麼意義（具體但不給買賣建議）4) 有什麼風險或不確定性。語氣親切易懂，避免術語堆疊。",
      "key_data": [
        {{
          "label": "指標名稱（例如：預估EPS、目標股價、毛利率）",
          "value": "數字本身（例如：95元、59.5%、1560元）",
          "context": "白話說明這個數字代表什麼意義（例如：代表台積電今年每股預計賺95元，比去年大幅成長）"
        }}
      ],
      "related_concepts": ["延伸學習的相關財經名詞或概念"]
    }}
  ]
}}

規則：
- summary 必須 150–200 字，用說故事的方式，專有名詞必須白話解釋，不能只堆術語
- position 必須說清楚立場和原因，不能只說「看多」「看空」或描述主題內容
- key_data 只收明確數字/百分比/價格/日期，純文字陳述不可放入，沒有數字就給 []
  每筆三個欄位都必須填：label（指標名稱）、value（數字）、context（白話說明）
  ✓ {{"label":"預估EPS","value":"95元","context":"代表台積電今年每股預計賺95元，比去年大幅成長"}}
  ✗ {{"label":"","value":"台股衝破28000點","context":""}}（value是文字，label和context是空的）
- 以初學者為目標讀者，沒有投資背景的人也能看懂
"""


def json_schema_description(mode: str = "pro", forced_topics: list = None) -> str:
    # ── 依模式產生不同的 summary / position / key_data 規則 ──────────────────
    if mode == "novice":
        summary_rule = (
            "- summary：完整段落（150–200 字），用說故事的方式依序寫：\n"
            "  1) 這個主題的背景是什麼（白話說清楚，專有名詞要解釋）\n"
            "  2) 發生了什麼事、為什麼普通人要在意\n"
            "  3) 對一般投資人代表什麼意義（具體但不給買賣建議）\n"
            "  4) 有什麼風險或不確定性\n"
            "  語氣親切易懂，避免術語堆疊，讓沒有投資背景的人也能理解\n"
        )
        position_rule = (
            "- position：說清楚 podcaster 的方向和原因，帶一點脈絡，讓新手知道「為什麼」\n"
            "  ✓ 「看好台積電，因為 AI 晶片需求持續拉動先進封裝訂單，產能已排到明年」\n"
            "  ✗ 「看多」（太短，沒有原因）\n"
        )
        key_data_rule = (
            "- key_data：只收明確的數字、百分比、價格、日期，純文字陳述不可放入，沒有數字就給 []\n"
            "  每筆三個欄位都必須填寫，不可留空：\n"
            "    label  = 這個數字的指標名稱（例如：「預估EPS」「目標股價」「毛利率」）\n"
            "    value  = 數字本身（例如：「95元」「59.5%」「1560元」）\n"
            "    context = 白話說明這個數字代表什麼意義（例如：「代表台積電今年每股預計賺95元，比去年大幅成長」）\n"
            "  ✓ {\"label\":\"預估EPS\",\"value\":\"95元\",\"context\":\"代表台積電今年每股預計賺95元，比去年大幅成長\"}\n"
            "  ✗ {\"label\":\"\",\"value\":\"台股衝破28000點\",\"context\":\"\"}（value是文字不是數字，label和context是空的）\n"
        )
    else:
        summary_rule = (
            "- summary：精簡段落（100–130 字），直接依序寫：\n"
            "  1) 核心論點/關鍵變數（直接說結論）\n"
            "  2) 催化劑或觸發條件（什麼事會讓這個論點成真）\n"
            "  3) 目標情境與時間框架（預期到什麼程度、何時）\n"
            "  4) 追蹤指標 or 否定條件（什麼訊號代表判斷錯了）\n"
            "  不解釋名詞，直接講信號、數字、可行動的結論\n"
        )
        position_rule = (
            "- position：直接說結論 + 觸發條件，有方向、有依據\n"
            "  ✓ 「看多，CoWoS 良率數據確認後加碼，目標 Q4 法說前」\n"
            "  ✓ 「觀望，等 HBM 良率超過 60% 才確認轉機」\n"
            "  ✗ 「討論了記憶體相關的各種面向」（沒有立場）\n"
        )
        key_data_rule = (
            "- key_data：盡量多收數字，精準附來源與時間點\n"
            "  每一筆必須是獨立的 {\"label\":\"...\",\"value\":\"...\",\"context\":\"...\"} 物件\n"
            "  ✓ [{\"label\":\"CoWoS月產能\",\"value\":\"16K片\",\"context\":\"2024Q4目標，較年初翻倍\"}]\n"
            "  ✗ [{\"label\":\"\",\"value\":\"毛利率59.5%；資本支出421億\",\"context\":\"\"}]\n"
        )

    if forced_topics:
        topics_list = "\n".join(f"  - 「{t}」" for t in forced_topics)
        forced_topics_block = (
            "【最高優先規則：主題清單鎖定】\n"
            "arguments 陣列必須嚴格且完整地包含以下所有主題，不可增加、刪除或修改任何字元（包括冒號、空格、括號）：\n"
            f"{topics_list}\n"
            "每一個 topic 字串必須與上方清單完全一致，連標點符號都不能改。\n"
            "違反此規則（自行增加主題、改變名稱、缺漏主題）視為格式錯誤。\n\n"
        )
    else:
        forced_topics_block = ""

    return f"""{forced_topics_block}
請輸出『合法 JSON』且只輸出 JSON（不要 ```json）。

頂層欄位一定要有：

{{
  "one_sentence_summary": "...",
  "investment_takeaways": {{
    "bullish": [],
    "bearish": [],
    "watchlist": [],
    "podcaster_stance": "看多|看空|觀望|混合/視情況"
  }},
  "classification": {{
    "financial_topics": [],
    "industries": [],
    "content_types": [],
    "markets": [],
    "asset_types": []
  }},
  "tags": [],
  "entities": {{
    "companies_or_stocks": [],
    "countries_or_regions": [],
    "people": []
  }},
  "arguments": [
    {{
      "topic": "...",
      "position": "...",
      "summary": "...",
      "key_data": [
        {{"label": "...", "value": "...", "context": "..."}}
      ],
      "related_concepts": [],
      "evidence_timestamps": []
    }}
  ],
  "outlook_calls": []
}}

規則：
- investment_takeaways 填法：
  目的是讓使用者快速掃描「這集值不值得看」，格式要具體、可行動。
  bullish / bearish / watchlist 每筆格式：「[具體標的] — [一句話關鍵理由]」
    ✓ 「台積電 — CoWoS 訂單滿載，AI 需求超預期」
    ✓ 「記憶體族群 — 供需反轉確認，Q3 開始漲價」
    ✗ 「市場整體看好」（太模糊）
    ✗ 「台積電表現不錯」（沒有理由）
  bullish：podcaster 明確看漲的股票、ETF、產業或指數
  bearish：明確看跌或提出風險警示的標的
  watchlist：尚無明確方向但值得追蹤的標的（如：等待觀察、待確認訊號）
  podcaster_stance：整集立場，從四選一：看多 / 看空 / 觀望 / 混合/視情況
  沒有對應內容就給空陣列，不要硬填
- arguments 切法：
  【固定結構型】以下三個主題若逐字稿有實質內容就必須出現，沒有才略過：
    「總體經濟環境」「操作策略與建議」「風險提示」
  【個股型】逐字稿中被認真討論（3 句以上）的每一支股票／ETF／指數，
    各自獨立一條，topic 命名為「個股：[名稱]」，例如「個股：台積電」
  【產業型】逐字稿中被認真討論（3 句以上）的每一個產業主題，
    各自獨立一條，topic 統一命名為「產業：[主題名稱]」，例如「產業：記憶體漲價」「產業：AI伺服器需求」「產業：PCB族群」
  【完整性要求】動筆前先掃描全文，列出所有被認真討論（3句以上）的主題清單，
    再逐一確認每個主題都有對應的 argument，不可因為沒有框架對應就遺漏
- 同一主題若在逐字稿不同地方反覆出現，必須合併成同一個 arguments item，不可拆開
{position_rule}{summary_rule}{key_data_rule}- evidence_timestamps：從逐字稿（m:ss）挑 1–5 個最相關的
- one_sentence_summary：用一句話列出這集涵蓋的主要股票、產業主題、總體議題，
  不加立場與判斷，讓使用者判斷這集是否有他想找的資訊，65 字以內
  ✓ 「台積電、鴻海、記憶體族群、PCB上游材料為主軸，同步討論聯準會轉鷹對市場節奏的影響與MSCI成分股調整帶來的資金流向」
  ✗ 「市場因聯準會轉鷹而震盪，但AI、記憶體及PCB產業仍有結構性成長動能」（有立場、有判斷）
- classification 各欄位只能從固定白名單中選，不得自創
- tags 欄位固定輸出 [] 即可，系統後處理會自動根據 classification 生成
- outlook_calls 欄位先固定輸出 []，後續會由另一個步驟單獨補上

"""
