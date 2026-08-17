# apps/knowledge_graph/services/basket_purpose.py
"""
使用者選的「投資目的」對應到內部策略。UI 不會讓使用者直接選
Supply / Substitute / Co-impact，只會看到投資目的的白話描述。
"""

PURPOSE_CHOICES = [
    {
        "key": "supply_beneficiary",
        "label": "供應鏈上游",
        "description": "提供原材料、零組件或技術",
        "strategy": "supply_upstream",
    },
    {
        "key": "supply_customer",
        "label": "供應鏈下游",
        "description": "使用或整合其產品或技術",
        "strategy": "supply_downstream",
    },
    {
        "key": "diversify_exposure",
        "label": "競爭對手",
        "description": "在相同市場競爭",
        "strategy": "substitute",
    },
    {
        "key": "same_theme",
        "label": "共同主題",
        "description": "在相關領域或趨勢上共同受影響",
        "strategy": "co_impact",
    },
]

_STRATEGY_BY_KEY = {p["key"]: p["strategy"] for p in PURPOSE_CHOICES}


def get_strategy_for_purpose(purpose_key: str) -> str | None:
    return _STRATEGY_BY_KEY.get(purpose_key)
