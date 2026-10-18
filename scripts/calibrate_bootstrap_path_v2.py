# scripts/calibrate_bootstrap_path_v2.py
"""
針對 bootstrap_path_simulator_v2.py（log空間、逐日套用shift/widen、10%硬上限）
的CRPS校準，流程跟 calibrate_bootstrap_path.py（v1版）完全對應，差別只在：

  1. STEP 3 要存「每一天的原始log報酬率抽樣矩陣」(n_simulations × n_steps)，
     不能只存v1那樣的terminal_returns一維陣列——因為v2的shift/widen是逐日套用
     再累加，沒辦法像v1那樣直接對已經算好的終點值做簡單代數運算，網格搜尋時
     每一組候選參數都要重新做一次cumsum+clip+exp()。
  2. 因為(1)，這支腳本的計算量比v1重很多，先用縮小過的規模(較少模擬次數、
     較小網格、抽樣部分episode)驗證流程可行、看跑多久，再決定要不要衝正式規模。

用法：python scripts/calibrate_bootstrap_path_v2.py
"""
import os
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

import numpy as np
from django.utils import timezone
from apps.summaries.models import StockSentimentScore, BacktestingRecord
from concurrent.futures import ThreadPoolExecutor, as_completed

HISTORY_YEARS = 5
MIN_HISTORY_DAYS = 250
N_SIMULATIONS = 500          # 原型跑得比預期快很多，正式規模跟v1一致拉回500
TRAIN_FRACTION = 0.7
RNG_SEED = 42
MAX_EPISODES = 10_000        # 不再抽樣，實質上等於全部約2500筆都用
DAILY_MOVE_CAP = 0.10
LOG_CAP_HIGH = np.log(1 + DAILY_MOVE_CAP)
LOG_CAP_LOW = np.log(1 - DAILY_MOVE_CAP)

# 網格縮小一點(v1是12*10=120組，這裡8*7=56組)，先求跑得完、看大致落在哪個範圍，
# 之後如果要精修可以再針對最佳值附近加密。
MACRO_GRID = [0.02, 0.05, 0.08, 0.15, 0.25, 0.40, 0.60, 0.80]
RISK_GRID = [0.2, 0.5, 0.8, 1.2, 1.6, 2.2, 3.0]

print("=" * 70)
print("STEP 1：撈取候選紀錄(跟v1同一套邏輯)")
print("=" * 70)

all_scored = list(
    StockSentimentScore.objects.using("summariesdb")
    .filter(base__isnull=False, annual_vol__isnull=False)
    .select_related("summary")
    .values("id", "summary_id", "ticker", "risk_score", "macro_score", "summary__published_at")
)
summary_ids = [r["summary_id"] for r in all_scored]
bt_rows = (
    BacktestingRecord.objects.using("summariesdb")
    .filter(summary_id__in=summary_ids, start_time__isnull=False, end_time__isnull=False)
    .values("summary_id", "ticker", "start_time", "end_time")
)
months_by_key = {}
for bt in bt_rows:
    key = (bt["summary_id"], bt["ticker"])
    days = (bt["end_time"] - bt["start_time"]).days
    if days > 0:
        months_by_key[key] = max(1, round(days / 30))

now = timezone.now()
rows = []
for r in all_scored:
    key = (r["summary_id"], r["ticker"])
    months = months_by_key.get(key)
    if months is None:
        continue
    target = r["summary__published_at"] + timedelta(days=months * 30)
    if target > now:
        continue
    r["horizon_months"] = months
    rows.append(r)

print(f"有真實論述期間且已到期的候選紀錄數：{len(rows)}")

rng_pick = np.random.default_rng(RNG_SEED)
if len(rows) > MAX_EPISODES:
    pick_idx = rng_pick.choice(len(rows), size=MAX_EPISODES, replace=False)
    rows = [rows[i] for i in sorted(pick_idx)]
print(f"原型抽樣後：{len(rows)}筆")

tickers = sorted(set(r["ticker"] for r in rows))
print(f"涉及股票數：{len(tickers)}")


def fetch_ticker_history(ticker: str):
    import yfinance as yf
    ticker_rows = [r for r in rows if r["ticker"] == ticker]
    dates = [r["summary__published_at"].date() for r in ticker_rows]
    max_horizon_days = max(r["horizon_months"] for r in ticker_rows) * 31 + 10
    start = min(dates) - timedelta(days=int(HISTORY_YEARS * 365.25) + 10)
    end = max(dates) + timedelta(days=max_horizon_days)
    try:
        hist = yf.Ticker(ticker).history(
            start=start.isoformat(), end=end.isoformat(), interval="1d", auto_adjust=True,
        )["Close"].dropna()
    except Exception as e:
        return ticker, None, str(e)
    if hist.empty:
        return ticker, None, "empty"
    closes = {ts.date(): float(v) for ts, v in hist.items() if v > 0}
    return ticker, closes, None


print()
print("=" * 70)
print("STEP 2：抓歷史股價")
print("=" * 70)
t0 = time.time()
price_by_ticker = {}
failed_tickers = []
with ThreadPoolExecutor(max_workers=10) as pool:
    futures = {pool.submit(fetch_ticker_history, t): t for t in tickers}
    for fut in as_completed(futures):
        ticker, closes, err = fut.result()
        if closes is None:
            failed_tickers.append((ticker, err))
        else:
            price_by_ticker[ticker] = closes
print(f"成功：{len(price_by_ticker)}，失敗：{len(failed_tickers)} ({time.time()-t0:.0f}s)")


def nearest_close(closes, target, max_lookback=10):
    for back in range(max_lookback + 1):
        d = target - timedelta(days=back)
        if d in closes:
            return closes[d]
    return None


print()
print("=" * 70)
print("STEP 3：組出每筆紀錄的「逐日log報酬率抽樣矩陣」(不是v1的終點分布)")
print("=" * 70)

samples = []
skipped = 0
t0 = time.time()
for i, r in enumerate(rows):
    ticker = r["ticker"]
    closes = price_by_ticker.get(ticker)
    if closes is None:
        skipped += 1
        continue
    asof = r["summary__published_at"].date()
    months = r["horizon_months"]
    n_steps = max(1, round(months / 12 * 252))
    start_price = nearest_close(closes, asof)
    target_date = asof + timedelta(days=months * 30)
    actual_price = nearest_close(closes, target_date)
    if start_price is None or actual_price is None:
        skipped += 1
        continue

    window_start = asof - timedelta(days=int(HISTORY_YEARS * 365.25))
    hist_dates = sorted(d for d in closes if window_start <= d <= asof)
    if len(hist_dates) < MIN_HISTORY_DAYS:
        skipped += 1
        continue
    hist_prices = np.array([closes[d] for d in hist_dates])
    log_returns = np.diff(np.log(hist_prices))

    rng = np.random.default_rng(RNG_SEED + r["id"])
    draws = rng.choice(log_returns, size=(N_SIMULATIONS, n_steps), replace=True)  # (N_SIM, n_steps)
    mean_draw = draws.mean(axis=0, keepdims=True)  # (1, n_steps)，每組(macro,risk)共用，先算好省時間

    samples.append({
        "draws": draws,
        "mean_draw": mean_draw,
        "n_steps": n_steps,
        "start_price": start_price,
        "actual_price": actual_price,
        "risk_score": r["risk_score"],
        "macro_score": r["macro_score"],
    })
    if (i + 1) % 100 == 0:
        print(f"  進度 {i+1}/{len(rows)} ({time.time()-t0:.0f}s)")

print(f"有效樣本數：{len(samples)}（跳過 {skipped} 筆，耗時{time.time()-t0:.0f}s）")

rng_split = np.random.default_rng(RNG_SEED)
idx = rng_split.permutation(len(samples))
n_train = int(len(samples) * TRAIN_FRACTION)
train_idx, test_idx = idx[:n_train], idx[n_train:]
train_samples = [samples[i] for i in train_idx]
test_samples = [samples[i] for i in test_idx]
print(f"訓練集：{len(train_samples)}　測試集：{len(test_samples)}")


def v2_terminal_returns(s, macro_max, risk_max):
    """套v2公式(逐日、log空間、10%硬上限)，回傳這筆樣本的terminal_returns陣列。"""
    shift_per_day = (s["macro_score"] * macro_max) / s["n_steps"]
    widen = 1 + s["risk_score"] * risk_max
    adj = s["mean_draw"] + (s["draws"] - s["mean_draw"]) * widen + shift_per_day
    adj = np.clip(adj, LOG_CAP_LOW, LOG_CAP_HIGH)
    cum = adj.sum(axis=1)  # 終點的累積log報酬率
    return np.exp(cum) - 1  # 轉成算術報酬率


def crps_from_sorted(sorted_x, y):
    n = len(sorted_x)
    term1 = np.mean(np.abs(sorted_x - y))
    i = np.arange(1, n + 1)
    term2 = np.sum((2 * i - n - 1) * sorted_x) / (n * n)
    return float(term1 - term2)


def evaluate_crps(sample_list, macro_max, risk_max):
    crps_values = []
    for s in sample_list:
        terminal = np.sort(v2_terminal_returns(s, macro_max, risk_max))
        y_r = s["actual_price"] / s["start_price"] - 1
        crps_values.append(crps_from_sorted(terminal, y_r))
    return float(np.nanmean(crps_values)), float(np.nanmedian(crps_values))


def evaluate_coverage(sample_list, macro_max, risk_max, coverage):
    alpha = 1 - coverage
    low_pct = (1 - coverage) / 2 * 100
    high_pct = 100 - low_pct
    hits = 0
    widths = []
    scores = []
    for s in sample_list:
        terminal = v2_terminal_returns(s, macro_max, risk_max)
        L_r, U_r = np.percentile(terminal, low_pct), np.percentile(terminal, high_pct)
        y_r = s["actual_price"] / s["start_price"] - 1
        width_r = U_r - L_r
        if y_r < L_r:
            penalty = (2 / alpha) * (L_r - y_r)
        elif y_r > U_r:
            penalty = (2 / alpha) * (y_r - U_r)
        else:
            penalty = 0.0
            hits += 1
        scores.append(width_r + penalty)
        widths.append(width_r * 100)
    n = len(sample_list)
    return hits / n * 100, float(np.nanmean(widths)), float(np.nanmean(scores))


print()
print("=" * 70)
print("STEP 4：CRPS網格搜尋最佳(MACRO_SHIFT_MAX, RISK_WIDEN_MAX)")
print("=" * 70)
t0 = time.time()
best_crps = None
n_done = 0
n_total = len(MACRO_GRID) * len(RISK_GRID)
for macro_max in MACRO_GRID:
    for risk_max in RISK_GRID:
        train_crps_mean, _ = evaluate_crps(train_samples, macro_max, risk_max)
        n_done += 1
        if np.isnan(train_crps_mean):
            continue
        if best_crps is None or train_crps_mean < best_crps[0]:
            best_crps = (train_crps_mean, macro_max, risk_max)
        if n_done % 10 == 0:
            print(f"  網格進度 {n_done}/{n_total} ({time.time()-t0:.0f}s)")
print(f"網格搜尋耗時 {time.time()-t0:.0f}s")

crps_train_mean, crps_macro_max, crps_risk_max = best_crps
crps_train_mean2, crps_train_median = evaluate_crps(train_samples, crps_macro_max, crps_risk_max)
crps_test_mean, crps_test_median = evaluate_crps(test_samples, crps_macro_max, crps_risk_max)
print(
    f"\nv2 CRPS最佳參數 = (MACRO_SHIFT_MAX={crps_macro_max}, RISK_WIDEN_MAX={crps_risk_max})　"
    f"訓練CRPS 平均={crps_train_mean2:.4f} 中位數={crps_train_median:.4f}　"
    f"測試CRPS 平均={crps_test_mean:.4f} 中位數={crps_test_median:.4f}"
)

print()
print("=" * 70)
print(f"用v2最佳參數，檢查30~90%各涵蓋率的實際表現")
print("=" * 70)
COVERAGE_LEVELS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
print(f"{'宣稱涵蓋率':>8} {'訓練實際':>9} {'訓練落差':>8} {'訓練寬度%':>9} {'測試實際':>9} {'測試落差':>8} {'測試寬度%':>9}")
for coverage in COVERAGE_LEVELS:
    tc, tw, _ = evaluate_coverage(train_samples, crps_macro_max, crps_risk_max, coverage)
    sc, sw, _ = evaluate_coverage(test_samples, crps_macro_max, crps_risk_max, coverage)
    print(f"{coverage*100:>7.0f}% {tc:>8.1f}% {tc-coverage*100:>+7.1f}pp {tw:>8.1f}% "
          f"{sc:>8.1f}% {sc-coverage*100:>+7.1f}pp {sw:>8.1f}%")

print()
print("=" * 70)
print("對照：v1正式系統在相同這批樣本上的CRPS(套用v1公式，方便直接比較)")
print("=" * 70)


def v1_terminal_returns(s, macro_max, risk_max):
    raw = np.exp(s["draws"].sum(axis=1)) - 1  # v1終點是純log報酬率加總再exp()，沒有逐日shift/widen
    mean_r = raw.mean()
    shift = s["macro_score"] * macro_max
    widen = 1 + s["risk_score"] * risk_max
    return mean_r + (raw - mean_r) * widen + shift


def evaluate_crps_v1(sample_list, macro_max, risk_max):
    crps_values = []
    for s in sample_list:
        terminal = np.sort(v1_terminal_returns(s, macro_max, risk_max))
        y_r = s["actual_price"] / s["start_price"] - 1
        crps_values.append(crps_from_sorted(terminal, y_r))
    return float(np.nanmean(crps_values)), float(np.nanmedian(crps_values))


v1_train_mean, v1_train_median = evaluate_crps_v1(train_samples, 0.08, 0.5)
v1_test_mean, v1_test_median = evaluate_crps_v1(test_samples, 0.08, 0.5)
print(
    f"v1現行參數(0.08/0.5)在這批樣本上：訓練CRPS 平均={v1_train_mean:.4f} 中位數={v1_train_median:.4f}　"
    f"測試CRPS 平均={v1_test_mean:.4f} 中位數={v1_test_median:.4f}"
)
