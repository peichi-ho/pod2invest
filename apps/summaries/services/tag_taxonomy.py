# apps/summaries/services/tag_taxonomy.py
from typing import Dict, List


FINANCIAL_TOPICS = [
    "總體經濟",
    "金融市場",
    "投資策略",
    "產業分析",
    "公司分析",
    "科技趨勢",
    "政策與地緣政治",
]

INDUSTRIES = [
    "半導體",
    "人工智慧",
    "電動車",
    "能源",
    "金融",
    "生技醫療",
    "消費科技",
    "雲端 / SaaS",
    "區塊鏈 / 加密貨幣",
    "電商",
    "媒體娛樂",
    "製造業",
    "房地產",
]

CONTENT_TYPES = [
    "新聞",
    "訪談",
    "深度分析",
    "投資觀點",
    "案例研究",
]

MARKETS = [
    "美股",
    "台股",
    "港股",
    "加密貨幣",
    "外匯",
    "總體貨幣",
]

ASSET_TYPES = [
    "ETF",
    "個股",
    "債券",
    "貴金屬",
]


CLASSIFICATION_KEYS = [
    "financial_topics",
    "industries",
    "content_types",
    "markets",
    "asset_types",
]

CLASSIFICATION_ALLOWED_MAP: Dict[str, List[str]] = {
    "financial_topics": FINANCIAL_TOPICS,
    "industries": INDUSTRIES,
    "content_types": CONTENT_TYPES,
    "markets": MARKETS,
    "asset_types": ASSET_TYPES,
}


def empty_classification() -> dict:
    return {
        "financial_topics": [],
        "industries": [],
        "content_types": [],
        "markets": [],
        "asset_types": [],
    }


def dedupe_str_list(values) -> List[str]:
    out = []
    seen = set()

    if not isinstance(values, list):
        return []

    for v in values:
        s = str(v).strip()
        if not s:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def filter_allowed(values, allowed: List[str]) -> List[str]:
    allowed_set = set(allowed)
    out = []
    seen = set()

    if not isinstance(values, list):
        return []

    for v in values:
        s = str(v).strip()
        if not s:
            continue
        if s in allowed_set and s not in seen:
            seen.add(s)
            out.append(s)

    return out


def normalize_classification_dict(classification) -> dict:
    if not isinstance(classification, dict):
        classification = {}

    out = empty_classification()
    for key in CLASSIFICATION_KEYS:
        out[key] = filter_allowed(
            classification.get(key, []),
            CLASSIFICATION_ALLOWED_MAP[key],
        )
    return out


def flatten_classification_to_tags(classification: dict) -> List[str]:
    """
    為了向下相容保留 tags 欄位，但 tags 不再自由生成，
    而是由 classification 展平而來。
    """
    normalized = normalize_classification_dict(classification)

    out = []
    seen = set()
    for key in CLASSIFICATION_KEYS:
        for item in normalized.get(key, []):
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def classification_rules_text() -> str:
    return (
        "【固定分類白名單】\n"
        f"- financial_topics 只能從以下選：{FINANCIAL_TOPICS}\n"
        f"- industries 只能從以下選：{INDUSTRIES}\n"
        f"- content_types 只能從以下選：{CONTENT_TYPES}\n"
        f"- markets 只能從以下選：{MARKETS}\n"
        f"- asset_types 只能從以下選：{ASSET_TYPES}\n"
        "規則：\n"
        "1) 不得自創分類值\n"
        "2) 不確定就輸出 []\n"
        "3) 同一欄位可多選，但只能選白名單中的值\n"
    )
