# apps/calculator/services/scenario.py
"""
情境資產成長模擬（試算計算機核心邏輯）。

已驗證內容（見 Task #1~#4）：
  - spread 公式用 annual_vol（波動率）驅動寬度，不是報酬率。
  - annual_vol 對 spread 的貢獻設上限 VOL_CAP，避免極端投機股數字失控。
  - 參數已用182筆真實資料、70/30訓練測試切分校準過（測試集覆蓋率65.5%，
    優於demo初步參數的50.9%）。因為資料庫目前只涵蓋15個月AI供應鏈特殊多頭期，
    這組參數是「目前資料限制下的最佳解」，不是最終定案。
  - GBM 模擬：基準情境獨立跑 N_PATHS 次算 10~90 百分位區間帶（誠實呈現不確定性）；
    樂觀/悲觀維持單一「示範路徑」，跟基準示範路徑共用同一組隨機亂數（避免不合理交錯，
    且線條保留真實震盪感，不是平滑曲線）。極端波動率股票（如 annual_vol > 100%）下，
    示範路徑可能因單次隨機抽樣運氣脫離區間帶之外，屬已知限制。
"""
import math
import random
from dataclasses import dataclass, field

# ── 已校準參數（Task #4，測試集覆蓋率65.5%，見專案說明文件）───────────────────
K = 1.2
R_MAX = 1.0
L_MAX = 0.4
ADD = 0.4
VOL_CAP = 0.60      # annual_vol 對 spread 的貢獻上限
BEAR_FLOOR = -0.90  # 悲觀情境安全下限

N_PATHS = 1000       # 基準情境統計區間帶的模擬次數
BAND_LOW_PCT = 10
BAND_HIGH_PCT = 90
MONTHS = 12


@dataclass
class ScenarioReturns:
    bull: float
    base: float
    bear: float


def compute_scenario_returns(base: float, annual_vol: float, risk_score: float, macro_score: float) -> ScenarioReturns:
    """核心公式：算出樂觀/基準/悲觀三個情境的年化報酬率。"""
    vol_for_spread = min(annual_vol, VOL_CAP)
    spread = K * (1 + R_MAX * risk_score) * vol_for_spread + ADD * (0.3 + risk_score)
    lean = L_MAX * macro_score

    bull = base + spread * (1 + lean)
    bear = max(base - spread * (1 - lean), BEAR_FLOOR)
    return ScenarioReturns(bull=bull, base=base, bear=bear)


def _simulate_path(annual_return: float, annual_vol: float, start_price: float,
                    months: int, shocks: list[float]) -> list[float]:
    """用給定的隨機震盪序列，跑一條 GBM 路徑（月為單位）。"""
    monthly_drift = math.log(1 + annual_return) / 12
    monthly_vol = annual_vol / math.sqrt(12)
    price = start_price
    row = [price]
    for z in shocks:
        price = price * math.exp(monthly_drift + monthly_vol * z - 0.5 * monthly_vol ** 2)
        row.append(price)
    return row


def _percentile(values: list[float], pct: float) -> float:
    values = sorted(values)
    idx = (len(values) - 1) * pct / 100
    lo, hi = int(math.floor(idx)), int(math.ceil(idx))
    if lo == hi:
        return values[lo]
    frac = idx - lo
    return values[lo] * (1 - frac) + values[hi] * frac


@dataclass
class ScenarioChartData:
    months: list[int]
    bull_line: list[float]
    base_line: list[float]      # 基準「示範路徑」，跟樂觀/悲觀共用隨機亂數
    bear_line: list[float]
    base_band_low: list[float]  # 基準統計區間帶（獨立跑N_PATHS次，跟上面示範線是兩回事）
    base_band_high: list[float]
    returns: ScenarioReturns = field(default_factory=lambda: ScenarioReturns(0, 0, 0))


def build_scenario_chart(base: float, annual_vol: float, risk_score: float, macro_score: float,
                          start_price: float, seed: int = None) -> ScenarioChartData:
    """
    給定輸入，回傳畫圖用的完整資料：
    三條有真實震盪的示範線 + 基準的統計區間帶。
    """
    returns = compute_scenario_returns(base, annual_vol, risk_score, macro_score)

    rng = random.Random(seed)
    shared_shocks = [rng.gauss(0, 1) for _ in range(MONTHS)]

    bull_line = _simulate_path(returns.bull, annual_vol, start_price, MONTHS, shared_shocks)
    base_line = _simulate_path(returns.base, annual_vol, start_price, MONTHS, shared_shocks)
    bear_line = _simulate_path(returns.bear, annual_vol, start_price, MONTHS, shared_shocks)

    band_rng = random.Random((seed or 0) + 1000)
    band_paths = []
    for _ in range(N_PATHS):
        shocks = [band_rng.gauss(0, 1) for _ in range(MONTHS)]
        band_paths.append(_simulate_path(returns.base, annual_vol, start_price, MONTHS, shocks))

    band_low, band_high = [], []
    for m in range(MONTHS + 1):
        col = [p[m] for p in band_paths]
        band_low.append(_percentile(col, BAND_LOW_PCT))
        band_high.append(_percentile(col, BAND_HIGH_PCT))

    return ScenarioChartData(
        months=list(range(MONTHS + 1)),
        bull_line=bull_line,
        base_line=base_line,
        bear_line=bear_line,
        base_band_low=band_low,
        base_band_high=band_high,
        returns=returns,
    )
