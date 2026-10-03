# apps/calculator/services/bootstrap_path_simulator_v2.py
"""
B方案(歷史拔靴)的重新設計原型——不覆蓋、不修改 bootstrap_path_simulator.py，
兩份並存方便直接比較實測效果，正式環境目前還是用原版。

起因(2026-10-01)：原版把podcast語氣調整(shift/widen)套用在「算術報酬率、累積到
當天的落差」上，實測發現兩個問題：
  1. 縮放沒有下限保護，極端情況下算術報酬率會跌破-100%，股價變成負數，
     反推單日漲跌幅時跑出300%+這種荒謬數字(不是誇飾，真的實測跑出來過)。
  2. 縮放力道集中灌在「累積落差變化最大的那幾天」，通常剛好是歷史上原本漲跌
     已經比較大的日子，縮放完超過台股漲跌停10%的比例，連中等設定(risk_score=0.5)
     都有約0.4~0.7%的模擬日會超過。

這個版本的設計改動：
  A. 整個搬到log報酬率空間操作，最後才用exp()轉回股價——股價結構上不可能變負，
     不是加個下限去擋，是log空間本身的數學性質保證的。
  B. shift/widen套用在「每一天自己抽到的報酬率」上，不是套在「累積到當天的
     落差」上——縮放力道平均分攤到每一天，不會集中爆發在特定幾天。
  C. 平移量(立場分數/drift)改成線性均勻分攤到每一天(shift_per_day = 總平移量/
     n_steps)，不再用sqrt時間漸強——drift項目統計上該隨時間線性累積，sqrt漸強
     比較適合用在不確定性(widen)身上，兩者不該共用同一種時間分布規則，這是
     重新設計時額外修正的一點，不只是搬空間而已。
  D. 縮放倍數(widen_target)维持常數，不再額外乘tau——套在每天自己的報酬率上
     再累加，天生就會有從0開始平滑成長的曲線(隨機漫步本身的性質)，不需要再
     疊加一層人工的時間漸強，疊加reversed反而是問題(A)(B)的部分成因。
  E. 額外加一個明確的硬上限(DAILY_MOVE_CAP=10%)：不管縮放/平移算出來多極端，
     單日隱含漲跌幅最後都會被夾在±10%內，保證不會出現不合理的單日跳動，
     代價是被夾到的那一小撮天數(實測約0.6~1%)，數值不是真實該有的隨機值，
     是用一點點寫實度換取「保證合規」。

已校準內容(2026-10-01，見 scripts/calibrate_bootstrap_path_v2.py)：
  用跟v1當初校準同一批2501筆真實(節目×股票)紀錄(1750訓練/751測試)，拿CRPS
  網格搜尋出這個新公式下的最佳參數 MACRO_SHIFT_MAX=0.08、RISK_WIDEN_MAX=0.8，
  測試集CRPS=0.2635，比v1現行參數(0.08/0.5)在同一批樣本上的0.2712更低。

  意外發現：v2在30%~90%全部測試過的涵蓋率，實際涵蓋率跟宣稱涵蓋率的落差都在
  2pp以內，80%涵蓋率測試集落差甚至是0.0pp——v1當初正是因為80%涵蓋率落差
  高達4.6pp，才被迫從P10~P90縮到P30~P70。v2似乎沒有v1「涵蓋率越寬、落差越大」
  的那個弱點，理論上可以考慮用更寬的區間(例如拿回P10~P90)同時維持好的校準
  精準度，但目前BAND_LOW_PCT/BAND_HIGH_PCT仍沿用v1的P30~P70，要不要改成
  更寬的區間是還沒拍板的產品決定，不是校準結果本身的限制。

2026-10-01再改動：拿寬度真的套到台積電/聯發科這種真實個股身上看過之後，P10~P90
對單一個股來說數字太誇張(台積電積極情境可以到180%+)——這不是校準錯誤，是這5年
AI半導體超級多頭期真實發生過的歷史(有實際驗證過台積電過去5年滾動12個月報酬率，
P90確實高達109%)，但對使用者來說太嚇人。另外也實測過Interval Score、Pinball Loss
想找「客觀上最佳」的涵蓋率，結論是這件事沒有純統計能唯一決定的答案，選多寬本質上
是產品判斷。最後決定：**圖表灰色區間帶維持P10~P90**(誠實呈現真實的不確定性範圍)，
但「保守情境/積極情境」這兩個具體點**改成P25~P75**(50%涵蓋率，數字比較收斂、不
嚇人)，兩者刻意脫鉤——灰色區塊負責「讓使用者知道真實世界有多大的可能範圍」，
保守/積極這兩個點負責「給一個沒那麼極端、比較好消化的參考數字」，各自取獨立的
統計量，不是同一組數字的兩種呈現方式。

用法：跟 bootstrap_path_simulator.run_bootstrap_path_simulation() 介面幾乎一樣，
多回傳 n_days_clipped(監控硬上限觸發頻率)、conservative_return/aggressive_return
(P25/P75終點報酬，給「保守情境/積極情境」這兩個標籤用，注意不是p10_return/
p90_return——那兩個欄位繼續對應BAND_LOW_PCT/BAND_HIGH_PCT=10/90，是灰色區間帶
用的，兩組欄位刻意分開，呼叫端不要混用)。
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np

from apps.calculator.services.bootstrap_path_simulator import (
    DEFAULT_HISTORY_PERIOD,
    TRADING_DAYS_PER_YEAR,
    _fetch_daily_log_returns,
)

# P10~P90(80%涵蓋率)，不是沿用v1的P30~P70——v1當初退到P30~P70是因為80%涵蓋率在
# v1公式下校準失準(測試集落差4.6pp)，v2重新校準後80%涵蓋率測試集落差是0.0pp，
# 全部測試過的涵蓋率(30%~90%)裡最準的一個，原本逼團隊放棄P10~P90的限制已經
# 不存在了，改回來同時保留住「保守~積極情境」該涵蓋大部分合理情況的原始用意。
BAND_LOW_PCT = 10
BAND_HIGH_PCT = 90

# 「保守情境/積極情境」這兩個具體數字用的百分位，刻意跟上面的BAND_LOW_PCT/
# BAND_HIGH_PCT脫鉤——見上方docstring「2026-10-01再改動」。50%涵蓋率(P25~P75)
# 是目前選定的預設值，之後如果要調整，只要改這兩個常數，不影響灰色區間帶。
CONSERVATIVE_PCT = 25
AGGRESSIVE_PCT = 75

# CRPS校準出來的參數(見上方docstring「已校準內容」)，跟v1公式不共用同一組數字——
# 因為計算空間換了(log空間、逐日套用、10%硬上限)，同樣寫0.08，兩邊公式算出來的
# 實際縮放力道並不相同，v2這組是針對v2公式本身重新搜出來的最佳值，不是沿用v1。
MACRO_SHIFT_MAX = 0.08
RISK_WIDEN_MAX = 0.8

# 單日隱含漲跌幅的硬上限，對應台股實際漲跌停——見上方docstring(E)。
DAILY_MOVE_CAP = 0.10
_LOG_CAP_HIGH = np.log(1 + DAILY_MOVE_CAP)
_LOG_CAP_LOW = np.log(1 - DAILY_MOVE_CAP)


@dataclass
class BootstrapPathSimulationResultV2:
    n_simulations: int
    time_steps: list[float]
    median_path: list[float]
    band_low: list[float]
    band_high: list[float]
    conservative_path: list[float]  # P25整條路徑，不是只有終點，畫圖用
    aggressive_path: list[float]    # P75整條路徑
    sample_paths: list[list[float]]
    terminal_returns: list[float]
    median_return: float
    p10_return: float
    p90_return: float
    conservative_return: float  # P25終點報酬，「保守情境」標籤用，見上方docstring
    aggressive_return: float    # P75終點報酬，「積極情境」標籤用
    history_days_used: int
    n_days_clipped: int  # 被硬上限夾住的(模擬路徑,天)組合數，用來監控clip觸發頻率


def run_bootstrap_path_simulation_v2(
    ticker: str,
    start_price: float,
    horizon_months: int,
    risk_score: float = 0.0,
    macro_score: float = 0.0,
    n_simulations: int = 1000,
    n_sample_paths: int = 50,
    history_period: str = DEFAULT_HISTORY_PERIOD,
    as_of_date: Optional[date] = None,
    seed: Optional[int] = None,
) -> BootstrapPathSimulationResultV2:
    if start_price <= 0:
        raise ValueError("start_price 必須是正數")
    if horizon_months <= 0:
        raise ValueError("horizon_months 必須是正數")

    return_pool = _fetch_daily_log_returns(ticker, period=history_period, as_of_date=as_of_date)

    horizon_years = horizon_months / 12
    n_steps = max(1, round(horizon_years * TRADING_DAYS_PER_YEAR))

    rng = np.random.default_rng(seed)
    draws = rng.choice(return_pool, size=(n_simulations, n_steps), replace=True)

    n_clipped = 0
    if macro_score != 0 or risk_score != 0:
        mean_draw = draws.mean(axis=0, keepdims=True)
        widen_target = 1 + risk_score * RISK_WIDEN_MAX
        shift_per_day = (macro_score * MACRO_SHIFT_MAX) / n_steps
        adj_draws = mean_draw + (draws - mean_draw) * widen_target + shift_per_day

        clipped_mask = (adj_draws > _LOG_CAP_HIGH) | (adj_draws < _LOG_CAP_LOW)
        n_clipped = int(clipped_mask.sum())
        adj_draws = np.clip(adj_draws, _LOG_CAP_LOW, _LOG_CAP_HIGH)
    else:
        adj_draws = draws

    cum_log_returns = np.cumsum(adj_draws, axis=1)
    cum_log_returns = np.hstack([np.zeros((n_simulations, 1)), cum_log_returns])
    price_paths = start_price * np.exp(cum_log_returns)
    terminal_returns = price_paths[:, -1] / start_price - 1

    band_low = np.percentile(price_paths, BAND_LOW_PCT, axis=0)
    band_high = np.percentile(price_paths, BAND_HIGH_PCT, axis=0)
    median_path = np.percentile(price_paths, 50, axis=0)
    conservative_path = np.percentile(price_paths, CONSERVATIVE_PCT, axis=0)
    aggressive_path = np.percentile(price_paths, AGGRESSIVE_PCT, axis=0)

    sample_idx = rng.choice(n_simulations, size=min(n_sample_paths, n_simulations), replace=False)
    sample_paths = price_paths[sample_idx]

    time_steps = list(np.linspace(0, horizon_years, n_steps + 1))

    return BootstrapPathSimulationResultV2(
        n_simulations=n_simulations,
        time_steps=time_steps,
        median_path=median_path.tolist(),
        band_low=band_low.tolist(),
        band_high=band_high.tolist(),
        conservative_path=conservative_path.tolist(),
        aggressive_path=aggressive_path.tolist(),
        sample_paths=sample_paths.tolist(),
        terminal_returns=terminal_returns.tolist(),
        median_return=float(np.median(terminal_returns)),
        p10_return=float(np.percentile(terminal_returns, BAND_LOW_PCT)),
        p90_return=float(np.percentile(terminal_returns, BAND_HIGH_PCT)),
        conservative_return=float(np.percentile(terminal_returns, CONSERVATIVE_PCT)),
        aggressive_return=float(np.percentile(terminal_returns, AGGRESSIVE_PCT)),
        history_days_used=len(return_pool),
        n_days_clipped=n_clipped,
    )
