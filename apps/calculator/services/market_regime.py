# apps/calculator/services/market_regime.py
"""
市場環境(牛/熊)標記，給 bootstrap_path_simulator_v2 的「空頭日占比」旋鈕用。

用途：不是預測現在是牛市還是熊市，而是把抽樣母體裡的每一個歷史日子標成「空頭日」或
「多頭日」，讓使用者可以自己決定抽樣時兩種日子各占多少比例(例如偏空＝空頭日抽到的
機率比歷史實際比例高)。預設不啟用，輸出跟沒有這個功能時完全一樣。

標記規則：20%規則(事後標記)。從高點回落20%以上算空頭(高點到低點)，從低點反彈20%
以上算多頭(低點到高點)。這是事後才確定轉折點的描述性標記，只拿來描述「歷史上哪些
日子屬於空頭」，不拿來判斷「現在」是不是熊市。標記只用到模擬基準日(as_of_date)以前
的指數資料，不會偷看之後。

基準指數：台股(.TW/.TWO)用加權指數，其他用S&P 500。
"""
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

BEAR_DROP_THRESHOLD = 0.20
CONTEXT_YEARS = 4            # 往窗口之前多抓幾年指數，讓窗口起點附近的轉折點不被截斷
MIN_DAYS_PER_REGIME = 60     # 任一種日子少於這個天數(約3個月)就視為樣本太薄，不套用
BEAR_SHARE_MIN = 0.05
BEAR_SHARE_MAX = 0.95
# 偏多／偏空按鈕的「空頭日占比」(絕對水位，不是歷史占比的倍數)。
# 2026-10-13 依 scripts/calibrate_regime_multiplier.py 的經驗校準改成絕對水位：
#   真熊市年(後12個月指數報酬 <= -10%)的實際空頭日占比中位數：台股約56%、美股約72%，
#   且與「過去5年的占比」呈負相關(台-0.48、美-0.62)，乘上倍數不是好的描述；
#   真牛市年(>= +20%)實際約0~9%。故偏空取60%、偏多取8%。
# 為了不讓按鈕反向：偏空至少維持歷史實際占比、偏多至多維持歷史實際占比。
PRESET_LEVEL = {"bull": 0.08, "bear": 0.60}


def benchmark_for(ticker: str) -> str:
    return "^TWII" if ticker.upper().endswith((".TW", ".TWO")) else "^GSPC"


def _fetch_closes(symbol: str, start: date, end: date) -> pd.Series:
    import yfinance as yf

    s = yf.Ticker(symbol).history(
        start=start.isoformat(), end=end.isoformat(), interval="1d", auto_adjust=True,
    )["Close"].dropna()
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    return s


def bear_labels(close: pd.Series, thr: float = BEAR_DROP_THRESHOLD) -> pd.Series:
    """回傳跟 close 同索引的序列：1=多頭日、0=空頭日(20%規則，事後標記)。"""
    n = len(close)
    labels = np.ones(n)
    if n < 2:
        return pd.Series(labels, index=close.index)

    px = close.values
    pivots = []                 # (位置, "peak"|"trough")
    trend = None
    hi_i = lo_i = 0
    for i in range(1, n):
        p = px[i]
        if p > px[hi_i]:
            hi_i = i
        if p < px[lo_i]:
            lo_i = i
        if trend in (None, "up") and p <= px[hi_i] * (1 - thr):
            pivots.append((hi_i, "peak"))
            trend, lo_i = "down", i
        elif trend in (None, "down") and p >= px[lo_i] * (1 + thr):
            pivots.append((lo_i, "trough"))
            trend, hi_i = "up", i

    if not pivots:
        return pd.Series(labels, index=close.index)

    first_i, first_kind = pivots[0]
    labels[: first_i + 1] = 1 if first_kind == "peak" else 0
    for (i0, k0), (i1, _) in zip(pivots, pivots[1:]):
        labels[i0 : i1 + 1] = 0 if k0 == "peak" else 1
    last_i, last_kind = pivots[-1]
    labels[last_i:] = 0 if last_kind == "peak" else 1
    return pd.Series(labels, index=close.index)


def build_regime_weights(
    pool_dates: pd.DatetimeIndex,
    ticker: str,
    as_of_date: Optional[date],
    preset: Optional[str] = None,
    bear_share: Optional[float] = None,
):
    """
    回傳 (weights, info)。weights 是跟抽樣母體等長的機率陣列(總和1)，None 代表不套用
    (任何原因退回均勻抽樣)，info 一定有值，給前端顯示用。

    preset: "bull"(空頭日約8%)/"bear"(空頭日約60%，且不低於歷史實際)；bear_share: 直接指定(0~1)，優先於preset。
    """
    benchmark = benchmark_for(ticker)
    info = {"benchmark": benchmark, "applied": False, "warning": None}

    try:
        end = (as_of_date or date.today()) + timedelta(days=1)
        start = pool_dates.min().date() - timedelta(days=int(CONTEXT_YEARS * 365.25))
        idx_close = _fetch_closes(benchmark, start, end)
        if len(idx_close) < 250:
            raise ValueError("基準指數資料不足")
    except Exception as e:
        info["warning"] = f"抓不到基準指數({benchmark})，改用歷史全部日子抽樣：{e}"
        return None, info

    lab = bear_labels(idx_close).reindex(pool_dates, method="ffill")
    if lab.isna().any():
        lab = lab.fillna(1.0)
    is_bear = (lab.values == 0)
    n_bear, n_bull = int(is_bear.sum()), int((~is_bear).sum())
    actual = n_bear / max(len(is_bear), 1)
    info.update({"actual_bear_share": actual, "n_bear_days": n_bear, "n_bull_days": n_bull})

    if min(n_bear, n_bull) < MIN_DAYS_PER_REGIME:
        info["warning"] = (
            f"這段期間的{'空頭' if n_bear < n_bull else '多頭'}日只有{min(n_bear, n_bull)}天，"
            f"樣本太少，改用歷史全部日子抽樣"
        )
        return None, info

    if bear_share is not None:
        target = float(bear_share)
    elif preset in PRESET_LEVEL:
        level = PRESET_LEVEL[preset]
        target = max(level, actual) if preset == "bear" else min(level, actual)
    else:
        return None, info
    target = min(max(target, BEAR_SHARE_MIN), BEAR_SHARE_MAX)

    w = np.where(is_bear, target / n_bear, (1 - target) / n_bull)
    info.update({"applied": True, "applied_bear_share": target})
    return w / w.sum(), info
