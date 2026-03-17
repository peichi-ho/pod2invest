import sqlite3
import json
import re
import os
import google.generativeai as genai

# ==========================================
# 參數與全域設定
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "podcast_graph.sqlite3")

# 記得替換成你的 API Key，並保持 transport="rest" 避免卡住！
GOOGLE_API_KEY = "AIzaSyAqnpsSllMo8ncEpkjHkOauWSUqgrbhe_Q" 
genai.configure(api_key=GOOGLE_API_KEY, transport="rest")
model = genai.GenerativeModel('gemini-2.5-flash') 

# ==========================================
# 模組 1: 讓 LLM 當裁判，智能合併重複或相似的原因
# ==========================================
def aggregate_reasons_with_llm(source: str, target: str, reasons: list) -> list:
    """傳入多條原因，讓 LLM 合併相似語意，回傳乾淨的陣列"""
    if len(reasons) == 1:
        return reasons # 只有一條就不用浪費 API 額度啦！

    prompt = f"""
    Task: 你是一位精準的商業分析師。請檢視以下 {source} 與 {target} 之間的幾條關聯原因（可能來自不同集的 Podcast）。
    請將「語意高度相似、描述同一件事」的敘述合併；如果是「不同的商業事件」，請保留為獨立項目。
    
    【原始原因清單】：
    {json.dumps(reasons, ensure_ascii=False, indent=2)}

    Output Format:
    請務必只輸出 JSON 格式的字串陣列 (Array of Strings)，用 ```json 包裝。
    範例：["輝達委託台積電代工3奈米晶片", "雙方共同研發矽光子封裝技術"]
    """
    
    try:
        response = model.generate_content(prompt)
        json_match = re.search(r'```json\n(.*?)\n```', response.text, re.DOTALL | re.IGNORECASE)
        
        if json_match:
            return json.loads(json_match.group(1))
        else:
            # 容錯處理：如果 AI 忘記加 markdown 標記
            return json.loads(response.text)
    except Exception as e:
        print(f"⚠️ [{source}->{target}] LLM 合併失敗，退回原始清單。錯誤: {e}")
        return reasons # 萬一 API 壞掉，至少把原始資料吐出去，不要讓程式崩潰
# ==========================================
# 模組 2: 負責查資料庫並組裝 JSON 的主函式 (支援動態篩選)
# ==========================================
def get_graph_data(start_date: str = None, end_date: str = None, target_industry: str = None) -> str:
    """根據時間與產業動態篩選資料，並產出 D3.js 格式的 JSON"""
    
    # 建立友善的 Log 訊息
    filter_msg = []
    if target_industry: filter_msg.append(f"產業={target_industry}")
    if start_date and end_date: filter_msg.append(f"時間={start_date}~{end_date}")
    msg = "、".join(filter_msg) if filter_msg else "全部資料 (無篩選)"
    print(f"🔍 開始查詢資料庫... 條件：{msg}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. 預先撈出所有 Node 的產業資訊，建立成字典方便快速查詢
    cursor.execute("SELECT id, industry FROM nodes")
    node_info = {row[0]: row[1] for row in cursor.fetchall()}

    # 2. 動態組裝 SQL 查詢 Links (處理時間篩選)
    query = "SELECT source, target, relation_type, reason FROM links"
    params = []
    if start_date and end_date:
        query += " WHERE summary_date BETWEEN ? AND ?"
        params.extend([start_date, end_date])
        
    cursor.execute(query, params)
    raw_links = cursor.fetchall()

    # 3. 核心邏輯：過濾與分組 (處理產業篩選)
    grouped_links = {}
    connected_nodes = set() 
    
    for source, target, relation_type, reason in raw_links:
        # 如果有設定 target_industry，就檢查 source 或 target 是否符合
        if target_industry:
            source_ind = node_info.get(source, "")
            target_ind = node_info.get(target, "")
            
            # 只要兩端都不屬於目標產業，就跳過這條線
            if source_ind != target_industry and target_ind != target_industry:
                continue 

        # 通過篩選，記錄真正有連線的節點
        connected_nodes.add(source)
        connected_nodes.add(target)
        
        # 使用 (source, target, relation_type) 當作群組的 Key
        key = (source, target, relation_type)
        if key not in grouped_links:
            grouped_links[key] = []
        grouped_links[key].append(reason)

    # 4. 準備產出 D3.js 需要的結構
    # 從字典中抓回原始產業名稱
    final_nodes = [{"id": n, "industry": node_info.get(n, "未分類")} for n in connected_nodes]
    final_links = []

    print(f"🧠 開始聚合關聯... 共有 {len(grouped_links)} 組連線需要處理。")
    
    for (source, target, relation_type), reasons in grouped_links.items():
        weight = len(reasons) 
        merged_reasons = aggregate_reasons_with_llm(source, target, reasons)
        
        final_links.append({
            "source": source,
            "target": target,
            "relation_type": relation_type,
            "weight": weight,
            "reasons": merged_reasons 
        })

    conn.close()
    
    # 打包成最終 JSON
    graph_data = {
        "nodes": final_nodes,
        "links": final_links
    }
    
    print("✅ JSON 資料組裝完成！")
    return json.dumps(graph_data, ensure_ascii=False, indent=2)

# ==========================================
# 執行範例
# ==========================================
if __name__ == "__main__":
    # --- 你可以自由開關以下的註解來測試不同的篩選情境 ---

    # 情境 A: 兩者都篩選
    # result_json = get_graph_data(start_date="2026-01-01", end_date="2026-12-31", target_industry="半導體")
    
    # 情境 B: 只篩選時間 (看這段時間內「所有產業」發生的事)
    # result_json = get_graph_data(start_date="2026-01-01", end_date="2026-12-31")
    
    # 情境 C: 只篩選產業 (看該產業「從古至今」的所有關聯)
    result_json = get_graph_data(target_industry="半導體")
    
    # 情境 D: 什麼都不篩選 (看整顆資料庫的宇宙全貌)
    # result_json = get_graph_data()

    # 寫入檔案
    output_file = os.path.join(BASE_DIR, "d3_ready_graph.json")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(result_json)
        
    print(f"\n🎉 成功匯出至 {output_file}！")