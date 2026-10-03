# apps/calculator/services/gbm_path_simulator.py
"""
真正「逐步跑」的 GBM 路徑模擬——回應教授對 scenario.py 現有三條情境線的批評：
現在的 bull_line/base_line/bear_line 是先用公式算出三個閉式報酬率數值，再共用同一組
隨機震盪畫成三條「示範線」，線本身沒有攜帶模擬資訊，教授說得對，這樣的圖跟畫三個數字
沒有本質差異。

這裡刻意跟 scenario.py 完全獨立、不共用任何函式/常數——故意重新做一份，方便直接比較
「現在的做法」vs「真的逐步模擬1000次」兩種設計哪個更好，不是要取代 scenario.py。

做法：把投資期間切成很多小步驟(預設每日)，每一步都從標準常態分布N(0,1)獨立抽一個隨機值，
套用GBM遞迴公式往前推一步，重複跑 n_simulations 次，每次都是一條完全獨立、真正隨機
生成的路徑——不是算出終點再回頭畫線。最後把這些路徑的每個時間點取分位數，疊出一個
「真的跑出來的」信賴區間帶，而不是三條裝飾線。

risk_score/macro_score 怎麼套進GBM沒有業界統一標準，這裡的做法（風險分數加寬波動率、
總經分數平移趨勢中心）是全新設計、還沒有用真實資料校準參數是否合理——EFF_VOL_R_MAX/
EFF_VOL_CAP/DRIFT_LEAN_MAX 目前只是起始草稿值，跟 scenario.py 的K/R_MAX/L_MAX/ADD
(那組已用2454筆真實資料回測校準過)地位不一樣，用之前務必先回測驗證。
"""
from dataclasses import dataclass
from typing import Optional

import numpy as np

# ── 草稿參數，尚未用真實資料校準(見上方docstring) ─────────────────────────
EFF_VOL_R_MAX = 1.0      # risk_score 對波動率的最大加成倍數
EFF_VOL_CAP = 1.20       # effective_vol 上限，避免極端股票波動率失控
DRIFT_LEAN_MAX = 0.15    # macro_score 對年化drift的最大平移幅度(對數報酬尺度)

DEFAULT_STEPS_PER_YEAR = 252  # 逐日模擬，比scenario.py的逐月(12步/年)更貼近真實GBM路徑
BAND_LOW_PCT = 10
BAND_HIGH_PCT = 90


@dataclass
class GbmPathSimulationResult:
    n_simulations: int
    time_steps: list[float]          # 以「年」為單位的時間軸，0 ~ horizon_years
    median_path: list[float]
    band_low: list[float]            # 每個時間點，n_simulations條路徑的第10百分位價格
    band_high: list[float]           # 每個時間點，第90百分位價格
    sample_paths: list[list[float]]  # 從n_simulations條路徑抽一小批(預設50條)給前端畫背景線
    terminal_returns: list[float]    # 每條路徑「終點」的報酬率，長度=n_simulations，給統計用
    median_return: float
    p10_return: float
    p90_return: float
    effective_vol: float
    effective_drift: float


def run_gbm_path_simulation(
    base: float,
    annual_vol: float,
    start_price: float,
    horizon_months: int,
    risk_score: float = 0.0,
    macro_score: float = 0.0,
    n_simulations: int = 1000,
    steps_per_year: int = DEFAULT_STEPS_PER_YEAR,
    n_sample_paths: int = 50,
    seed: Optional[int] = None,
) -> GbmPathSimulationResult:
    """
    base: 年化期望報酬率假設(例如podcast論點隱含的成長率，或現有系統算出的base)。
    annual_vol: 年化波動率。
    horizon_months: 模擬時間長度(月)，跟使用者選的投資期間對齊。
    risk_score/macro_score: 沿用專案既有的-1~1量表(podcast語氣的主觀判斷)。

    每一條路徑都是獨立抽1000次N(0,1)隨機數逐步生成，不是算出終點再回頭畫——
    這是這個模組存在的唯一理由，任何「先算出報酬率、再套進GBM解析公式直接跳到終點」
    的寫法都不符合這裡要驗證的設計，故意不提供那種捷徑。
    """
    if start_price <= 0:
        raise ValueError("start_price 必須是正數")
    if annual_vol < 0:
        raise ValueError("annual_vol 不能是負數")
    if horizon_months <= 0:
        raise ValueError("horizon_months 必須是正數")

    horizon_years = horizon_months / 12
    n_steps = max(1, round(horizon_years * steps_per_year))
    dt = horizon_years / n_steps

    effective_vol = min(annual_vol * (1 + EFF_VOL_R_MAX * risk_score), EFF_VOL_CAP)
    effective_vol = max(effective_vol, 0.0)
    effective_drift = np.log(1 + base) + macro_score * DRIFT_LEAN_MAX

    rng = np.random.default_rng(seed)
    # (n_simulations, n_steps) 矩陣，每個元素都是獨立抽的N(0,1)——這才是「真的跑」的核心：
    # 每一步、每一條路徑的隨機數都各自獨立，不是共用同一組震盪去畫不同情境。
    z = rng.standard_normal((n_simulations, n_steps))

    step_log_returns = (effective_drift - 0.5 * effective_vol ** 2) * dt + effective_vol * np.sqrt(dt) * z
    cum_log_returns = np.cumsum(step_log_returns, axis=1)
    # 每條路徑補回t=0的起點(價格=start_price, 對數報酬=0)
    cum_log_returns = np.hstack([np.zeros((n_simulations, 1)), cum_log_returns])
    price_paths = start_price * np.exp(cum_log_returns)  # shape: (n_simulations, n_steps+1)

    band_low = np.percentile(price_paths, BAND_LOW_PCT, axis=0)
    band_high = np.percentile(price_paths, BAND_HIGH_PCT, axis=0)
    median_path = np.percentile(price_paths, 50, axis=0)

    terminal_prices = price_paths[:, -1]
    terminal_returns = terminal_prices / start_price - 1

    sample_idx = rng.choice(n_simulations, size=min(n_sample_paths, n_simulations), replace=False)
    sample_paths = price_paths[sample_idx]

    time_steps = list(np.linspace(0, horizon_years, n_steps + 1))

    return GbmPathSimulationResult(
        n_simulations=n_simulations,
        time_steps=time_steps,
        median_path=median_path.tolist(),
        band_low=band_low.tolist(),
        band_high=band_high.tolist(),
        sample_paths=sample_paths.tolist(),
        terminal_returns=terminal_returns.tolist(),
        median_return=float(np.median(terminal_returns)),
        p10_return=float(np.percentile(terminal_returns, 10)),
        p90_return=float(np.percentile(terminal_returns, 90)),
        effective_vol=float(effective_vol),
        effective_drift=float(effective_drift),
    )
