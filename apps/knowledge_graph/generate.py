import sqlite3
import json
import re
import os
from datetime import datetime
from xmlrpc import client
from google import genai
from django.conf import settings

# ==========================================
# 參數與全域設定
# ==========================================
DB_PATH = "podcast_graph.sqlite3"

# 請替換為你的 Gemini API Key
_client = None

def get_model():
    global _client
    if _client is None:
        api_key = getattr(settings, "GEMINI_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "")
        _client = genai.Client(api_key=api_key)
    return _client

# ==========================================
# 模組 1: 資料庫建置與初始化
# ==========================================
def init_db():
    """初始化 SQLite 資料庫，建立 nodes 與 links 表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 開啟 Foreign Key 支援
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 建立 Nodes 表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            industry TEXT
        )
    ''')

    # 建立 Links 表 (加入 summary_date 與 podcast_source 方便未來 Filter)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            target TEXT,
            relation_type TEXT,
            reason TEXT,
            summary_date DATE,
            podcast_source TEXT,
            FOREIGN KEY(source) REFERENCES nodes(id),
            FOREIGN KEY(target) REFERENCES nodes(id)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ [系統] 資料庫初始化完成。")

# ==========================================
# 模組 2: 呼叫 LLM 執行 Prompt Step 1
# ==========================================
def extract_graph_from_summary(podcast_summary: str) -> str:
    # 修正後的 Prompt
    prompt_step1 = """
    Role: 你是一位產業鏈分析專家，擅長從文本中萃取企業與產業間的「價值網圖譜」。

    Task: 請閱讀我提供的「財經 Podcast 摘要」資料。請建立 `entities` 作為節點基礎，並剖析出「公司與公司」或「公司與產業」之間的直接關聯。

    Association Rules (建立連接 Link 的條件): 
    1. 垂直供應 (Supply): A 提供產品、服務或資源給 B。
    2. 趨勢共生 (Co-impact): 兩者共同受惠或受害於某一趨勢。
    3. 競爭替代 (Substitution): 兩者在同一市場競爭，或具備資源替代性。

    Output Format:
    請務必只輸出 JSON 格式的資料，用 ```json 包裝。
    必須包含:
    1. `nodes`: 包含 id, 以及 industry 屬性。
       *特別注意：請根據上下文與你的知識，為「每一個 node」獨立判斷並填寫其所屬的 `industry` (例如：半導體、金融業、電動車、航運等)。*
    2. `links`: 包含 source, target, reason, relation_type 屬性。
    """
    
    print("\n🧠 [階段一] 呼叫 LLM 大腦運算中... 正在萃取 JSON 結構資料...")
    # 將 Prompt 與摘要合併
    request_content = f"{prompt_step1}\n\n【Podcast 摘要內容】：\n{podcast_summary}"
    
    client = get_model()
    model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")
    response = client.models.generate_content(model=model_name, contents=request_content)

    output_text = response.text

    # 使用 Regex 抓取 JSON
    json_match = re.search(r'```json\n(.*?)\n```', output_text, re.DOTALL | re.IGNORECASE)
    
    if json_match:
        print("✅ [階段一] 成功從 LLM 萃取 JSON。")
        return json_match.group(1)
    else:
        print("❌ [階段一] 找不到 JSON 區塊，萃取失敗。")
        # 容錯處理：有時候 LLM 不會加上 ```json，直接返回純文字
        try:
            json.loads(output_text)
            return output_text
        except:
            print("❌ 原始輸出也非有效 JSON，印出原始回覆供 Debug:")
            print(output_text)
            return None

# ==========================================
# 模組 3: 寫入資料庫 (Upsert 邏輯)
# ==========================================
# 移除外部傳入的 industry 參數
def save_to_db(json_data_str: str, summary_date: str, podcast_source: str):
    """將 JSON 寫入 SQLite，動態讀取節點專屬的產業別"""
    try:
        data = json.loads(json_data_str)
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析失敗: {e}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. 寫入 Nodes (使用 UPSERT 邏輯)
    nodes = data.get("nodes", [])
    for node in nodes:
        node_id = node.get("id")
        node_industry = node.get("industry", "未分類") 
        
        # 修正：移除殘留的 group_id=excluded.group_id
        cursor.execute('''
            INSERT INTO nodes (id, industry)
            VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET 
                industry=excluded.industry
        ''', (node_id, node_industry))

    # 2. 寫入 Links (邏輯不變)
    links = data.get("links", [])
    inserted_links = 0
    for link in links:
        source = link.get("source")
        target = link.get("target")
        relation_type = link.get("relation_type", "Unknown")
        reason = link.get("reason", "")

        cursor.execute('''
            SELECT id FROM links 
            WHERE source = ? AND target = ? AND relation_type = ? AND summary_date = ?
        ''', (source, target, relation_type, summary_date))
        
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO links (source, target, relation_type, reason, summary_date, podcast_source)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (source, target, relation_type, reason, summary_date, podcast_source))
            inserted_links += 1

    conn.commit()
    conn.close()
    print(f"✅ [資料庫] 寫入完成！處理了 {len(nodes)} 個節點，並寫入 {inserted_links} 條關聯連線。")

# ==========================================
# 主程式：整合 Pipeline
# ==========================================
def process_new_podcast(summary_text: str, summary_date: str, podcast_source: str):
    print(f"\n🚀 開始處理新的 Podcast 摘要: {podcast_source} ({summary_date})")
    extracted_json = extract_graph_from_summary(summary_text)
    
    if extracted_json:
        save_to_db(
            json_data_str=extracted_json, 
            summary_date=summary_date, 
            podcast_source=podcast_source
        )
        

# ==========================================
# 執行範例
# ==========================================
if __name__ == "__main__":
    # 初始化資料庫 (確保表單存在)
    init_db()

    # 模擬你手邊有一篇新的 Podcast 摘要文本 (你未來可以用讀檔的方式取代)
    sample_summary_text = """
            {
  "one_sentence_summary": "本集 Podcast 深入探討 ETF 市場的演進與未來趨勢，從產品導向轉向解決方案導向，並強調主動式 ETF、連結式基金的創新，以及 AI 在投資配置與風險管理中的應用。節目也分析了美股與台股的配置策略，並指出台灣高股息環境的獨特性。",
  "investment_takeaways": {
    "bullish": [
      "AI 的發展將持續帶動美國企業獲利與生產力，進而推升美股指數。（白話：這代表後面可能會影響市場情緒/走勢）",
      "美國股市在全球資本市場中扮演火車頭角色，具有一定的韌性與支撐。（白話：這代表後面可能會影響市場情緒/走勢）",
      "台灣與美國的產業供應鏈緊密連結，美國市場的成長對台灣有受惠機會。（白話：這代表後面可能會影響市場情緒/走勢）",
      "連結式基金的發展有助於投資人更完善地進行資產配置，將 ETF 融入傳統基金平台。（白話：這代表後面可能會影響市場情緒/走勢）",
      "台灣獨特的高股息環境為投資人提供了與美國市場互補的資產配置選項。（白話：這代表後面可能會影響市場情緒/走勢）"
    ],
    "bearish": [
      "漲多的市場終將面臨修正，但美國仍具備引領全球資本市場的地位。（白話：這代表後面可能會影響市場情緒/走勢）",
      "過度集中於單一市場（如僅投資台灣）可能面臨系統性風險。（白話：這代表後面可能會影響市場情緒/走勢）（白話：這代表後面可能會影響市場情緒/走勢）"
    ],
    "watchlist": [
      "主動式 ETF 的發展與應用，以及其與傳統 ETF 和主動式基金的區別。（白話：這代表後面可能會影響市場情緒/走勢）",
      "連結式基金如何將 ETF 基金化，使其更容易融入投資組合配置。（白話：這代表後面可能會影響市場情緒/走勢）",
      "AI 在投資模擬、優化和風險評估中的潛在應用。（白話：這代表後面可能會影響市場情緒/走勢）",
      "美國股市的長期趨勢與潛在的修正風險。（白話：這代表後面可能會影響市場情緒/走勢）",
      "台灣高股息環境的獨特性與其在資產配置中的角色。（白話：這代表後面可能會影響市場情緒/走勢）",
      "地緣政治與國際經濟情勢對軍工國防產業的影響，以及其對未來規格變化的主導性。（白話：這代表後面可能會影響市場情緒/走勢）",
      "美國稅制改革、穩定幣發行等政策對資本市場的潛在影響。（白話：這代表後面可能會影響市場情緒/走勢）"
    ],
    "podcaster_stance": "混合/視情況"
  },
  "tags": [
    "#ETF",
    "#主動式ETF",
    "#連結式基金",
    "#資產配置",
    "#AI",
    "#美股",
    "#台股",
    "#投資策略",
    "#基金",
    "#高股息",
    "#被動投資",
    "#主動投資",
    "#投資組合",
    "#金融科技",
    "#AI投資",
    "#紀律投資",
    "#再投資"
  ],
  "entities": {
    "companies_or_stocks": [
      "元大投信",
      "0050",
      "0056",
      "00646",
      "輝達 (Nvidia)",
      "蘋果 (Apple)",
      "微軟 (Microsoft)",
      "S&P 500",
      "VOO",
      "SPY",
      "IVV",
      "台積電 (TSMC)",
      "Dimensional",
      "Vanguard",
      "BlackRock",
      "iShares",
      "OpenAI",
      "ChatGPT",
      "輝達",
      "蘋果",
      "微軟",
      "State Street"
    ],
    "countries_or_regions": [
      "台灣",
      "美國",
      "中國",
      "日本",
      "歐盟",
      "韓國"
    ],
    "people": [
      "劉忠勝",
      "約翰伯格 (John Bogle)",
      "約翰伯格"
    ]
  },
  "arguments": [
    {
      "topic": "ETF 市場的演進與發展趨勢",
      "position": "ETF 市場正從產品導向轉向解決方案導向，並朝向客製化與差異化發展。",
      "summary": "ETF 市場經歷了從傳統的市值型、高股息 ETF，到主動型 ETF、影子 ETF 等多元發展。過去投資人將 ETF 視為交易工具，未來則應轉變為配置工具，從單一檔交易導向轉向多檔投資組合策略。這種轉變意味著 ETF 市場正從「產品導向」邁向「解決方案導向」，從標準化走向客製化，並從價格敏感轉為品質敏感。元大投信作為早期發展者，已將產品線從單一股票報酬擴展至股票、債券、商品、外匯等多樣化資產類別，並包含不同風險等級的產品。未來市場的發展將更注重協助投資人建立投資組合，而非僅僅提供單一產品。這種轉變也意味著市場將從過去的「紅海」走向「藍海」，透過了解投資大眾的痛點，提供客製化的解決方案，而非單一產品賣給所有人。業者應與中介服務機構合作，提供更多附加價值與差異化服務，以提升投資人的投資體驗。",
      "key_data": [
        "元大投信ETF產品線已從單一股票報酬擴展至股票、債券、商品、外匯等四種資產類別。",
        "ETF市場正從1.0的產品導向轉向2.0的解決方案導向，強調客製化與品質敏感。"
      ],
      "related_concepts": [
        "指數股票型基金 (ETF)",
        "主動型基金",
        "市值型 ETF",
        "高股息 ETF",
        "資產配置",
        "投資組合",
        "解決方案導向",
        "客製化",
        "差異化",
        "紅海市場",
        "藍海市場"
      ],
      "evidence_timestamps": [
        "1:01",
        "1:21",
        "2:45",
        "2:52",
        "9:25",
        "9:33"
      ],
      "evidence_ranges": [
        {
          "start": "0:53",
          "end": "1:36"
        },
        {
          "start": "2:37",
          "end": "3:07"
        },
        {
          "start": "9:17",
          "end": "9:48"
        }
      ],
      "evidence_ui": [
        "0:53–1:36",
        "2:37–3:07",
        "9:17–9:48"
      ]
    },
    {
      "topic": "主動式 ETF 與傳統 ETF 的區別與整合",
      "position": "主動式 ETF 是主動基金與 ETF 的結合，並非完全對立，而是提供更多元的選擇。",
      "summary": "主動式 ETF 的出現，是為了滿足市場對於主動管理和 ETF 可交易性的雙重需求。傳統 ETF 以追蹤指數為目標，提供透明、低成本的被動投資；而主動式基金則追求超額報酬（Alpha）。主動式 ETF 結合了兩者的特性，既有主動基金的選股能力，又具備 ETF 的交易便利性。這種「你中有我，我中有你」的產品設計，使得投資人不再需要在主動與被動之間做絕對的選擇。美國的發展脈絡顯示，基金先指數基金化，再發展為 ETF，而台灣則跳過了指數基金階段，直接發展 ETF。主動式 ETF 的出現，讓投資人能更靈活地在不同市場和資產類別中進行配置，例如將其作為核心或衛星配置的一部分。其本質是將基金當股票買，進而實現基金股票化，而 ETF 則是將基金股票化，主動式 ETF 則是在此基礎上加入了主動選股的元素，提供更多元的投資策略選擇。",
      "key_data": [],
      "related_concepts": [
        "主動式基金",
        "被動式基金",
        "Alpha (超額報酬)",
        "Beta (市場報酬)",
        "指數化",
        "交易成本",
        "基金股票化",
        "主動式ETF",
        "ETF"
      ],
      "evidence_timestamps": [
        "11:12",
        "12:14",
        "18:28",
        "19:17",
        "24:50",
        "26:17"
      ],
      "evidence_ranges": [
        {
          "start": "11:04",
          "end": "11:27"
        },
        {
          "start": "12:06",
          "end": "12:29"
        },
        {
          "start": "18:20",
          "end": "18:43"
        }
      ],
      "evidence_ui": [
        "11:04–11:27",
        "12:06–12:29",
        "18:20–18:43"
      ]
    },
    {
      "topic": "連結式基金的創新與優勢",
      "position": "連結式基金透過將 ETF 基金化，解決了投資人資產配置的痛點，並提供不配息級別以實現再投資。",
      "summary": "連結式基金的出現，是為了將原本在股票市場交易的 ETF，無縫整合到傳統的基金平台或銀行財管通路中，解決了投資人跨平台操作的不便。過去，投資人若想將 ETF 納入投資組合，可能需要在證券市場開戶，並處理不同資金的流動與管理。連結式基金則將 ETF 視為基金的一種，使其能與境內外基金、主動型基金等一同配置。更關鍵的是，連結式基金推出了「不配息級別」，這對於追求複利效果的投資人至關重要。例如，0050 或 0056 的不配息級別連結基金，能將配息自動滾入本金再投資，避免了配息後價格被扣抵的問題，從而提升了長期投資的績效。這項創新使得投資人能更方便地進行資產配置，並實現「靜合」而非「零合」的市場發展。透過連結基金，投資人可以將台灣的 ETF（如 0050、0056）與美國的 ETF（如 00646）整合到同一平台，並與其他基金進行模擬與優化，大幅提升了資產配置的便利性與完善性。",
      "key_data": [
        "連結式基金將 ETF 基金化，使其能與傳統基金一同配置。",
        "連結式基金提供不配息級別，有助於投資人實現複利與再投資。",
        "0050、0056 等台灣 ETF 可透過連結基金整合至傳統財管平台。"
      ],
      "related_concepts": [
        "連結式基金",
        "ETF 基金化",
        "不配息級別",
        "再投資",
        "複利",
        "資產配置",
        "自由配",
        "靜合",
        "零合",
        "Simulation (模擬)",
        "Optimization (優化)"
      ],
      "evidence_timestamps": [
        "26:13",
        "28:20",
        "29:08",
        "30:07",
        "33:42",
        "34:33"
      ],
      "evidence_ranges": [
        {
          "start": "26:05",
          "end": "26:28"
        },
        {
          "start": "28:12",
          "end": "28:35"
        },
        {
          "start": "29:00",
          "end": "29:23"
        }
      ],
      "evidence_ui": [
        "26:05–26:28",
        "28:12–28:35",
        "29:00–29:23"
      ]
    },
    {
      "topic": "AI 在投資領域的應用與影響",
      "position": "AI 作為投資的輔助工具，能提升模擬、優化和風險評估的效率，但最終決策仍需由投資人自行判斷。",
      "summary": "AI 在投資領域的應用正日益廣泛，它能協助投資人進行更快速、更精準的投資模擬和優化。傳統的投資組合模型（如 CAPM、MPT）需要大量數據和複雜計算，而 AI 能夠大幅縮短資訊收集、數據處理和模型運算的時間。此外，AI 也能根據個人的需求，提供量身定制的投資方案，並協助識別潛在的未知風險。AI 能夠協助進行配置模擬、優化，並量測預期外的風險，提供判斷依據。然而，AI 並非萬能，它更像是投資的「合資公正」，能提供分析和判斷的依據，但最終的投資決策仍需回歸到投資人自身的風險承受能力、報酬期望以及個人偏好。AI 的發展將有助於提升投資效率，但無法取代人為的判斷與選擇。",
      "key_data": [
        "AI 可縮短資訊收集、數據處理和模型運算時間，提升投資模擬與優化效率。",
        "AI 可協助量身定制投資方案，並識別潛在風險。"
      ],
      "related_concepts": [
        "人工智慧 (AI)",
        "投資模擬",
        "投資組合優化",
        "風險評估",
        "量身定制",
        "CAPM 模型",
        "MPT 模型",
        "合資公正"
      ],
      "evidence_timestamps": [
        "39:20",
        "39:49",
        "40:05",
        "40:23",
        "41:26"
      ],
      "evidence_ranges": [
        {
          "start": "39:12",
          "end": "39:35"
        },
        {
          "start": "39:41",
          "end": "40:38"
        },
        {
          "start": "41:18",
          "end": "41:41"
        }
      ],
      "evidence_ui": [
        "39:12–39:35",
        "39:41–40:38",
        "41:18–41:41"
      ]
    },
    {
      "topic": "美股與台股的配置策略",
      "position": "美股在全球經濟中扮演火車頭角色，而台灣則因其產業連結和高股息環境具有互補性，兩者應結合配置。",
      "summary": "美股在全球資本市場中具有領導地位，其企業獲利和生產力受 AI 等科技發展的帶動，展現出強勁的成長動能。儘管漲多可能面臨修正，但其整體趨勢仍被看好。台灣與美國的經濟聯繫緊密，供應鏈上下游相互依存，且美國已成為台灣最大的貿易夥伴。因此，在進行資產配置時，同時納入美股和台股是合理的選擇。對於年輕投資人，可考慮以美國為核心，台灣為衛星，側重未來科技發展；而對於中產階級，則可結合股債、配息與不配息、台股與美股進行多元搭配。台灣的高股息環境是其獨特優勢，與美國缺乏高配息產品形成互補。整體而言，配置策略應考量年齡、風險偏好和熟悉度，以達到最佳的投資組合效果。美國股市的長期趨勢由 AI 等科技發展帶動，具有一定的韌性與支撐，但漲多終將面臨修正。台灣與美國的產業供應鏈緊密連結，美國市場的成長對台灣有受惠機會。年輕人可將美國作為核心，台灣為衛星；中產階級則可股債、配息與不配息、台股與美股多元搭配。台灣的高股息環境與美國市場形成互補。",
      "key_data": [
        "美國已取代中國成為台灣最大的貿易夥伴，台美供應鏈緊密連結。",
        "美國股市的 S&P 500 指數ETF規模龐大，顯示其全球吸引力。",
        "台灣高股息環境的股息率與配息率在全球名列前茅，與美國市場形成互補。"
      ],
      "related_concepts": [
        "美股",
        "台股",
        "供應鏈",
        "貿易",
        "高股息",
        "資產配置",
        "核心衛星策略",
        "系統風險",
        "AI",
        "生產力",
        "企業獲利",
        "地緣政治",
        "軍工國防",
        "漲跌幅限制"
      ],
      "evidence_timestamps": [
        "31:13",
        "31:54",
        "33:33",
        "36:55",
        "37:19",
        "37:30",
        "34:34",
        "34:43",
        "36:02",
        "36:38",
        "39:01"
      ],
      "evidence_ranges": [
        {
          "start": "31:05",
          "end": "31:28"
        },
        {
          "start": "31:46",
          "end": "32:09"
        },
        {
          "start": "33:25",
          "end": "33:48"
        }
      ],
      "evidence_ui": [
        "31:05–31:28",
        "31:46–32:09",
        "33:25–33:48"
      ]
    }
  ],
  "price_target_calls": []
}


    """

    # 執行管線 (每當有新摘要，就呼叫這個 Function)
    process_new_podcast(
        summary_text=sample_summary_text,
        summary_date="2026-03-12",       # 摘要發布或擷取的時間
        podcast_source="財經M平方 EP.100" # 來源標籤
    )