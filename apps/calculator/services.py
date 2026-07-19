# apps/calculator/services.py
"""
試算頁總經指標計分。

把「整集層」的 episode_macro/episode_risk（來自 SummaryRecord）跟
「個股層」的 direction/call_risk（來自 BacktestingRecord）合成
macro_score/risk_score，再算出樂觀/保守情境的報酬率假設，以及
Phase 1 GBM 模擬要用的波動度倍率放大係數。

macro_score ∈ [-1, 1]：總體/個股樂觀悲觀傾向，決定情境往哪偏
risk_score  ∈ [0, 1]：風險/不確定性強度，決定情境寬窄與波動放大程度

degenerate 檢查：macro_score=0 且 risk_score=0 時，compute_scenario_rates()
會精確退化回現行的固定公式（樂觀=基準×1.5、保守=基準×0.5，基準<0 時倍率互換），
sigma_amplifier() 也會回傳 1（Phase 1 現有的波動倍率不變）。
"""

_MACRO_LEVEL_SCORE = {
    "大幅樂觀": 1.0,
    "樂觀": 0.5,
    "中性": 0.0,
    "悲觀": -0.5,
    "大幅悲觀": -1.0,
}
_RISK_LEVEL_SCORE = {"低": 0.0, "中": 0.5, "高": 1.0}
_DIRECTION_SCORE = {"bullish": 1.0, "bearish": -1.0}

# 加權：個股層權重 > 整集層權重，因為使用者是在看特定股票
MACRO_WEIGHT_EPISODE = 0.3
MACRO_WEIGHT_CALL = 0.7
RISK_WEIGHT_EPISODE = 0.3
RISK_WEIGHT_CALL = 0.7

# spread/lean 公式參數（草稿值，上線後需依實際案例校準）
K_SPREAD = 0.5
R_MAX = 1.0
L_MAX = 0.6

# Phase 1 GBM 波動倍率（renderScenarioChart 的 SIGMA_MULT_* 常數，這裡只是後端算 amp 用）
SIGMA_MULT_BULL = 0.8
SIGMA_MULT_BASE = 1.0
SIGMA_MULT_BEAR = 1.3


def macro_level_to_score(level: str) -> float:
    return _MACRO_LEVEL_SCORE.get(level, 0.0)


def risk_level_to_score(level: str) -> float:
    return _RISK_LEVEL_SCORE.get(level, 0.0)


def direction_to_score(direction: str) -> float:
    return _DIRECTION_SCORE.get((direction or "").strip().lower(), 0.0)


def compute_macro_risk_scores(
    *, episode_macro: dict, episode_risk: dict, direction: str, call_risk: dict
) -> dict:
    """
    回傳 {"macro_score": ..., "risk_score": ...}。
    沒有個股層資料（direction 為空、call_risk 為空）時，call_macro/call_risk 視為 0，
    等同完全依賴整集層分數。
    """
    episode_macro_score = macro_level_to_score((episode_macro or {}).get("level"))
    episode_risk_score = risk_level_to_score((episode_risk or {}).get("level"))
    call_macro_score = direction_to_score(direction)
    call_risk_score = risk_level_to_score((call_risk or {}).get("level"))

    macro_score = MACRO_WEIGHT_EPISODE * episode_macro_score + MACRO_WEIGHT_CALL * call_macro_score
    risk_score = RISK_WEIGHT_EPISODE * episode_risk_score + RISK_WEIGHT_CALL * call_risk_score

    return {
        "macro_score": round(macro_score, 4),
        "risk_score": round(risk_score, 4),
    }


def compute_scenario_rates(base_rate: float, macro_score: float, risk_score: float) -> dict:
    """分數 → 樂觀/保守情境報酬率（基準情境本身不變，只影響樂觀/保守）。"""
    spread = K_SPREAD * (1 + R_MAX * risk_score) * abs(base_rate)
    lean = L_MAX * macro_score
    spread_up = spread * (1 + lean)
    spread_down = spread * (1 - lean)
    return {
        "bull_rate": round(base_rate + spread_up, 2),
        "bear_rate": round(base_rate - spread_down, 2),
        "spread": round(spread, 4),
        "lean": round(lean, 4),
    }


def sigma_amplifier(risk_score: float, r_max: float = R_MAX) -> float:
    """risk_score=0 時 amp=1，Phase 1 現有的 0.8/1.0/1.3 波動倍率完全不受影響。"""
    return 1 + r_max * risk_score
