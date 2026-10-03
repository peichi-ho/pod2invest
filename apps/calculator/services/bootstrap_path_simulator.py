# apps/calculator/services/bootstrap_path_simulator.py
"""
逐步「歷史拔靴」路徑模擬——回應教授對 scenario.py 現有三條情境線的批評，跟
gbm_path_simulator.py 是同一個問題的另一個獨立答案，兩者刻意不共用任何程式碼，
方便之後直接比較三套設計(舊的scenario.py / 新的常態亂數GBM / 這裡的歷史拔靴)。

跟 gbm_path_simulator.py 差在「每一步的隨機數從哪裡來」：
  gbm_path_simulator：每步從標準常態分布N(0,1)抽，假設報酬服從對數常態分布(GBM的
                       標準假設)，優點是有解析理論基礎，缺點是真實股價報酬通常比
                       常態分布更「厚尾」(極端漲跌比理論預期常見)。
  這裡(bootstrap)：   每步直接從這檔股票自己過去真實發生過的單日報酬率裡，隨機抽一筆
                       (有放回抽樣)。不假設任何分布形狀，路徑會自然帶有這檔股票真實
                       的厚尾/偏態特性，代價是完全依賴歷史資料涵蓋的期間夠不夠有代表性
                       (如果歷史期間剛好是特殊多頭/空頭，抽樣結果會帶有同樣的偏誤)。

注意跟使用者之前提的「直接抽過去12個月股價當一條路徑」不一樣：那樣只是重播歷史、
每次結果相同，不構成「跑1000次」；這裡是每一步「獨立」抽一筆歷史報酬，1000條路徑
會走出1000種完全不同的組合，才是真正的蒙地卡羅。

已驗證內容(2026-09-30 第一輪)：
  - podcast語氣調整(MACRO_SHIFT_MAX/RISK_WIDEN_MAX)已用2064筆真實(節目×股票)紀錄
    校準過(1444訓練/620測試)，P10~P90覆蓋率從66.6%(不調整)/68.1%(舊草稿值)
    提升到80.3%(訓練)/80.5%(測試)，訓練/測試落差僅0.2pp，不是過擬合。
  - 發現拔靴法本身(不套任何podcast調整)就會系統性低估真實不確定性(覆蓋率66.6%，
    離理論80%有明顯落差)，這是「每天獨立抽樣」打散真實市場連續性/趨勢延續效果
    的必然結果，不是實作錯誤。

已驗證內容(2026-09-30 第二輪，見 scripts/calibrate_bootstrap_path.py)：
  - 改用正式的 Interval Score/CRPS 評分(不再只用「|覆蓋率-目標|最小化」)，資料來源
    改成 StockSentimentScore 配對 BacktestingRecord 的「該集對該股票實際論述期間」
    (不是固定12個月)，篩出已到期、有真實後續報酬可查的2501筆(1750訓練/751測試)。
    用這個修正後的期間重新驗證第一輪參數(0.08/1.0)，算出訓練77.8%/測試80.2%，
    跟第一輪文件記載的80.3%/80.5%已經對得上，確認第一輪校準方法本身是對的。
  - 額外用CRPS(對整條模擬分布評分，不綁定任何特定涵蓋率)找出的單一最佳參數是
    MACRO_SHIFT_MAX=0.08、RISK_WIDEN_MAX=0.5，CRPS只比原參數(0.08/1.0)略好一點
    (測試0.2712 vs 0.2769)，代表原參數本來就接近最佳解。
  - 用CRPS最佳參數檢查30~90%各涵蓋率「實際覆蓋率 vs 宣稱覆蓋率」的落差，發現落差
    隨宣稱涵蓋率升高而擴大：30%只差1.3pp，80%卻差到4.6pp——拔靴法對「中段常見結果」
    校準得比較準，對「極端結果」的尾部校準天生比較弱。
  - 決定改用40%涵蓋率(P30~P70，見下方BAND_LOW_PCT/BAND_HIGH_PCT)，因為這是「宣稱
    覆蓋率跟實際覆蓋率貼合度」最好的區間(測試集只差0.5pp)；付出的代價是區間變窄
    (寬度約40% vs 原本80%版本的寬度約100~110%)，使用者看到的「可能範圍」會比較小。
  - 尚未驗證：HISTORY_YEARS(5年)、歷史窗口長度本身是否為最佳選擇；目前沿用
    scenario.py舊版驗證過的5年，沒有為此另外做校準。
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import numpy as np

DEFAULT_HISTORY_PERIOD = "5y"  # 抓幾年歷史日報酬當抽樣母體
HISTORY_YEARS = 5  # as_of_date 版本用：往回抓幾年，跟 DEFAULT_HISTORY_PERIOD 的年數保持一致，
                    # 故意維持5年不縮短成跟base/annual_vol一樣的1年——scenario.py舊版的
                    # bootstrap震盪來源就是抓5年歷史月報酬，已用2452筆真實資料回測驗證過
                    # (區間帶覆蓋率42.0%→43.4%)，沒有理由為了對齊時間窗口就捨棄這個已驗證
                    # 的選擇，兩者是「窗口該多長」跟「窗口該錨在哪一天」兩個獨立的問題。
MIN_HISTORY_DAYS = 250          # 少於這個天數(~1年)視為樣本太薄，直接丟例外不硬跑
TRADING_DAYS_PER_YEAR = 252

# P30~P70(40%涵蓋率)，不是原本的P10~P90(80%)——第二輪校準(2026-09-30)發現40%
# 涵蓋率是「宣稱覆蓋率 vs 實際覆蓋率」貼合度最好的區間(測試集只差0.5pp，80%那組
# 差到4.6pp)，見上方docstring「已驗證內容(第二輪)」。改動代價：使用者看到的區間會
# 比原本窄很多(寬度約40% vs 原本約100~110%)，是校準精準度換區間實用性的取捨。
BAND_LOW_PCT = 30
BAND_HIGH_PCT = 70

# ── podcast語氣調整——第二輪校準(2026-09-30，見 scripts/calibrate_bootstrap_path.py)
# 用CRPS(對整條模擬分布評分，不綁定單一涵蓋率)網格搜尋出的單一最佳參數，跟第一輪
# (0.08/1.0，用固定12個月投資期間假設校準)的CRPS只差一點點(測試0.2712 vs 0.2769)，
# 換算到P30~P70這個涵蓋率時，測試集實際覆蓋率40.5%、只比宣稱的40%多0.5pp。
MACRO_SHIFT_MAX = 0.08
RISK_WIDEN_MAX = 0.5


@dataclass
class BootstrapPathSimulationResult:
    n_simulations: int
    time_steps: list[float]
    median_path: list[float]
    band_low: list[float]
    band_high: list[float]
    sample_paths: list[list[float]]
    terminal_returns: list[float]
    median_return: float
    p10_return: float
    p90_return: float
    history_days_used: int


def _fetch_daily_log_returns(
    ticker: str, period: str = DEFAULT_HISTORY_PERIOD, as_of_date: Optional[date] = None,
) -> np.ndarray:
    """
    as_of_date 不給(None)時，維持原本行為：用yfinance的period參數，抓「執行當下」往回
    period年的資料。

    as_of_date 有給時，改成明確帶 start/end 日期，抓「as_of_date往回HISTORY_YEARS年
    ~ as_of_date」這段——只用 as_of_date 當時「已經發生過」的歷史，不會不小心把
    as_of_date之後才發生的價格變動也算進抽樣母體，重點是避免look-ahead bias
    (例如拿2026年才發生的真實漲跌，去模擬「假設現在是2025年」這個情境，等於
    偷看到了當時不可能知道的未來)。
    """
    import yfinance as yf

    if as_of_date is not None:
        start = as_of_date - timedelta(days=int(HISTORY_YEARS * 365.25))
        prices = yf.Ticker(ticker).history(
            start=start.isoformat(), end=as_of_date.isoformat(), interval="1d", auto_adjust=True,
        )["Close"].dropna()
    else:
        prices = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)["Close"].dropna()

    if len(prices) < MIN_HISTORY_DAYS:
        raise ValueError(f"{ticker} 歷史資料只有{len(prices)}個交易日，少於{MIN_HISTORY_DAYS}天，樣本太薄無法拔靴抽樣")
    log_prices = np.log(prices.values)
    return np.diff(log_prices)  # 每日對數報酬率序列


def run_bootstrap_path_simulation(
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
) -> BootstrapPathSimulationResult:
    """
    ticker: 用來抓真實歷史日報酬率當抽樣母體，這是這個模組跟 gbm_path_simulator 最大的
    不同——gbm不需要ticker(只需要annual_vol這個統計量)，這裡需要完整的歷史報酬序列。

    horizon_months: 投資期間(月)，換算成交易日數決定模擬要跑幾步。

    as_of_date: 給定時，歷史報酬率母體只抓 as_of_date 當時「已經發生過」的資料(見
    _fetch_daily_log_returns)，不是抓「現在」往回算。呼叫端要自己確保傳進來的
    start_price 也是 as_of_date 當天的股價(不是今天的即時價)，這裡不會幫忙檢查或
    自動抓——起點價格、抽樣母體、如果之後有用到的base/annual_vol，三者一定要
    錨在同一個日期，不然會出現「用今天的價格當起點、卻套用as_of_date當時的歷史
    分布」這種時間基準對不齊的問題。history_period 在 as_of_date 有給時不會被使用。

    每一步、每一條路徑都獨立從歷史報酬池裡「有放回」抽一筆——不是抽一段連續區間
    直接複製，每步都重新抽，1000條路徑才會長出1000種不同組合，符合「真的跑」的要求。
    """
    if start_price <= 0:
        raise ValueError("start_price 必須是正數")
    if horizon_months <= 0:
        raise ValueError("horizon_months 必須是正數")

    return_pool = _fetch_daily_log_returns(ticker, period=history_period, as_of_date=as_of_date)

    horizon_years = horizon_months / 12
    n_steps = max(1, round(horizon_years * TRADING_DAYS_PER_YEAR))

    rng = np.random.default_rng(seed)
    # 對 (n_simulations, n_steps) 矩陣裡的每一格，各自獨立從歷史報酬池抽一筆(有放回)。
    draws = rng.choice(return_pool, size=(n_simulations, n_steps), replace=True)

    cum_log_returns = np.cumsum(draws, axis=1)
    cum_log_returns = np.hstack([np.zeros((n_simulations, 1)), cum_log_returns])
    price_paths = start_price * np.exp(cum_log_returns)

    # 每個時間點都換算成「相對起始股價的報酬率」，podcast語氣調整才能套用在
    # 每一步，不是只套在終點——這裡修正了一個真實回報的不一致問題：舊版只調整
    # 終點的 terminal_returns，圖表線型/區間帶卻是從完全沒調整的 price_paths
    # 算出來的，導致「保守/中位數/積極情境終點報酬」這幾個統計數字跟圖表上
    # 同一個時間點的數字對不起來(圖表看起來沒套用風險/立場分數的調整)。
    returns_by_step = price_paths / start_price - 1

    # podcast語氣調整：用時間比例(tau)讓shift/widen的力道從第0個月的0，平滑增強到
    # 最後一個月的完整強度。第0個月調整力道天生是0，因為所有模擬路徑的起點都是
    # start_price，不需要特別處理。tau=1(終點)時，這裡算出來的 terminal_returns
    # 精確等於舊版只調整終點的算法，calibrate_bootstrap_path.py 校準過的
    # MACRO_SHIFT_MAX/RISK_WIDEN_MAX 數值不受這個改動影響。
    #
    # tau用sqrt(時間比例)而不是線性時間比例：這個模擬本質上是隨機漫步(每步獨立
    # 抽樣報酬率加總)，統計上的不確定性天生就是跟時間的平方根成長(標準差∝√t)，
    # 不是線性成長。改用sqrt讓shift/widen這層額外調整的成長速度，跟底層隨機漫步
    # 本身的不確定性成長速度同一種形狀(前期長得快、後期長得慢)，純粹是內插曲線
    # 形狀更合理，終點(tau=1)數值不受影響，中間月份仍然沒有真實資料驗證過。
    if macro_score != 0 or risk_score != 0:
        shift = macro_score * MACRO_SHIFT_MAX
        widen_target = 1 + risk_score * RISK_WIDEN_MAX
        tau = np.sqrt(np.linspace(0, 1, n_steps + 1))
        mean_by_step = returns_by_step.mean(axis=0, keepdims=True)
        effective_widen = 1 + tau * (widen_target - 1)
        effective_shift = tau * shift
        returns_by_step = mean_by_step + (returns_by_step - mean_by_step) * effective_widen + effective_shift
        price_paths = start_price * (1 + returns_by_step)

    terminal_returns = returns_by_step[:, -1]

    band_low = np.percentile(price_paths, BAND_LOW_PCT, axis=0)
    band_high = np.percentile(price_paths, BAND_HIGH_PCT, axis=0)
    median_path = np.percentile(price_paths, 50, axis=0)

    sample_idx = rng.choice(n_simulations, size=min(n_sample_paths, n_simulations), replace=False)
    sample_paths = price_paths[sample_idx]

    time_steps = list(np.linspace(0, horizon_years, n_steps + 1))

    return BootstrapPathSimulationResult(
        n_simulations=n_simulations,
        time_steps=time_steps,
        median_path=median_path.tolist(),
        band_low=band_low.tolist(),
        band_high=band_high.tolist(),
        sample_paths=sample_paths.tolist(),
        terminal_returns=terminal_returns.tolist(),
        median_return=float(np.median(terminal_returns)),
        # 欄位名稱維持 p10_return/p90_return(內部代稱，前端也還是讀這兩個key)，
        # 但實際百分位數要跟 band_low/band_high 用的 BAND_LOW_PCT/BAND_HIGH_PCT
        # 一致(目前是30/70)，不能寫死10/90——不然「保守/積極情境終點報酬」這兩個
        # 統計數字，會跟圖表上同一個時間點的保守/積極情境數值對不起來。
        p10_return=float(np.percentile(terminal_returns, BAND_LOW_PCT)),
        p90_return=float(np.percentile(terminal_returns, BAND_HIGH_PCT)),
        history_days_used=len(return_pool),
    )
