import json
import re
import os
from datetime import datetime, timedelta, timezone

from google import genai
from django.conf import settings
from django.db import connections


# ==========================================
# Gemini Client
# ==========================================
def _get_client():
    api_key = getattr(settings, "GEMINI_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "")
    return genai.Client(api_key=api_key)


# ==========================================
# 模組 1: LLM 合併相似 Reasons
# ==========================================
def aggregate_reasons_with_llm(source: str, target: str, reasons: list) -> list:
    """
    傳入同一組 (source, target) 的多條 reason，
    讓 LLM 合併語意相似的項目，不同事件則保留。
    只有一條時直接回傳，不呼叫 LLM。
    """
    if len(reasons) <= 1:
        return reasons

    prompt = (
        f"Task: 你是一位精準的商業分析師。請檢視以下 {source} 與 {target} 之間的幾條關聯原因（可能來自不同集的 Podcast）。\n"
        "請將「語意高度相似、描述同一件事」的敘述合併；如果是「不同的商業事件」，請保留為獨立項目。\n\n"
        f"【原始原因清單】：\n{json.dumps(reasons, ensure_ascii=False, indent=2)}\n\n"
        "Output Format:\n"
        "請務必只輸出 JSON 格式的字串陣列 (Array of Strings)，用 ```json 包裝。\n"
        '範例：["輝達委託台積電代工3奈米晶片", "雙方共同研發矽光子封裝技術"]'
    )

    try:
        client = _get_client()
        model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")
        response = client.models.generate_content(model=model_name, contents=prompt)
        json_match = re.search(r'```json\n(.*?)\n```', response.text, re.DOTALL | re.IGNORECASE)
        if json_match:
            return json.loads(json_match.group(1))
        return json.loads(response.text)
    except Exception as e:
        print(f"⚠️ [{source}->{target}] LLM 合併失敗，退回原始清單。錯誤: {e}")
        return reasons


# ==========================================
# 模組 2: 查詢並組裝 D3.js 格式資料
# ==========================================
def get_graph_data(
    start_date: str = None,
    end_date: str = None,
    industry: str = None,
) -> dict:
    """
    從 knowledge_graphdb 查詢 links 與 nodes，
    依條件篩選後回傳 D3.js 格式的 dict。

    篩選邏輯：
    - start_date / end_date：依 links.summary_date 過濾；兩者皆未填則預設為最近 7 天
    - industry：字串包含比對 nodes.industry；未填則不限制產業

    Links 的 reason 會透過 LLM 合併語意相似項目。
    """
    # 日期預設值：最近 7 天
    if not start_date and not end_date:
        today = datetime.now(timezone.utc).date()
        end_date = today.isoformat()
        start_date = (today - timedelta(days=7)).isoformat()
    elif start_date and not end_date:
        start_date_obj = datetime.fromisoformat(start_date) 
        end_date = (start_date_obj + timedelta(days=7)).isoformat()
    elif end_date and not start_date:
        end_date_obj = datetime.fromisoformat(end_date)
        start_date = (end_date_obj - timedelta(days=7)).isoformat()

    filter_parts = [f"日期 {start_date} ～ {end_date}"]
    if industry:
        filter_parts.append(f"產業含「{industry}」")
    print(f"🔍 查詢條件：{'、'.join(filter_parts)}")

    with connections["knowledge_graphdb"].cursor() as cursor:
        # 撈所有節點建立產業查找字典
        cursor.execute("SELECT name, industry FROM nodes")
        node_info = {row[0]: row[1] for row in cursor.fetchall()}

        # 查詢指定日期範圍的 links
        cursor.execute(
            "SELECT source, target, relation_type, reason FROM links "
            "WHERE summary_date BETWEEN %s AND %s",
            [start_date, end_date],
        )
        raw_links = cursor.fetchall()

    # Python 端做產業過濾（字串包含）並按 (source, target, relation_type) 分組
    grouped_links: dict[tuple, list] = {}
    connected_nodes: set[str] = set()

    for source, target, relation_type, reason in raw_links:
        if industry:
            source_ind = node_info.get(source, "")
            target_ind = node_info.get(target, "")
            if industry not in source_ind and industry not in target_ind:
                continue

        connected_nodes.add(source)
        connected_nodes.add(target)

        key = (source, target, relation_type)
        grouped_links.setdefault(key, []).append(reason)

    print(f"🧠 聚合 {len(grouped_links)} 組連線...")

    final_nodes = [
        {"id": name, "industry": node_info.get(name, "未分類")}
        for name in connected_nodes
    ]
    final_links = []

    for (source, target, relation_type), reasons in grouped_links.items():
        merged_reasons = aggregate_reasons_with_llm(source, target, reasons)
        final_links.append({
            "source": source,
            "target": target,
            "relation_type": relation_type,
            "weight": len(reasons),
            "reasons": merged_reasons,
        })

    print(f"✅ 查詢完成：{len(final_nodes)} 個節點，{len(final_links)} 條連線")
    return {"nodes": final_nodes, "links": final_links}
