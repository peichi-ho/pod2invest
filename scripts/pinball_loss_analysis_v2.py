# scripts/pinball_loss_analysis_v2.py
"""
用Pinball Loss(分位數損失)重新檢視「保守~積極情境該設在哪個涵蓋率」這個問題，
資料/模型設定跟 calibrate_bootstrap_path_v2.py 完全一樣(同一批2501筆、v2已校準
參數MACRO_SHIFT_MAX=0.08/RISK_WIDEN_MAX=0.8)，差別只在評分方式。

Pinball Loss跟「落差(coverage gap)」不一樣的地方：落差只看「有沒有蓋到」這個
二元結果(命中/沒命中)，Pinball Loss是連續的——沒蓋到的話，還要看「差多遠」，
蓋到的話也不是完全不計分(離得越靠中心分數越好)，資訊量比單純算命中率豐富。

對每個候選分位數τ(0.05~0.95)，各自獨立算「用這個分位數的模擬值去預測，
Pinball Loss平均是多少」，不像Interval Score那樣需要先綁定一個對稱的
(下界,上界)配對，也不用α縮放(IS的α縮放正是造成「不能跨涵蓋率比較」的原因)。

用法：python scripts/pinball_loss_analysis_v2.py
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
from apps.calculator.services.bootstrap_path_simulator_v2 import (
    MACRO_SHIFT_MAX, RISK_WIDEN_MAX, DAILY_MOVE_CAP,
)

HISTORY_YEARS = 5
MIN_HISTORY_DAYS = 250
N_SIMULATIONS = 500
TRAIN_FRACTION = 0.7
RNG_SEED = 42
LOG_CAP_HIGH = np.log(1 + DAILY_MOVE_CAP)
LOG_CAP_LOW = np.log(1 - DAILY_MOVE_CAP)

QUANTILES = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45,
             0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

print("=" * 70)
print("STEP 1：撈取候選紀錄(跟calibrate_bootstrap_path_v2.py同一套邏輯)")
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

print(f"候選紀錄數：{len(rows)}")
tickers = sorted(set(r["ticker"] for r in rows))


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
print("STEP 2：抓歷史股價")
t0 = time.time()
price_by_ticker = {}
with ThreadPoolExecutor(max_workers=10) as pool:
    futures = {pool.submit(fetch_ticker_history, t): t for t in tickers}
    for fut in as_completed(futures):
        ticker, closes, err = fut.result()
        if closes is not None:
            price_by_ticker[ticker] = closes
print(f"成功：{len(price_by_ticker)}/{len(tickers)} ({time.time()-t0:.0f}s)")


def nearest_close(closes, target, max_lookback=10):
    for back in range(max_lookback + 1):
        d = target - timedelta(days=back)
        if d in closes:
            return closes[d]
    return None


print()
print("STEP 3：用v2已校準參數，算出每筆樣本的terminal_returns分布")
t0 = time.time()
samples = []
skipped = 0
for i, r in enumerate(rows):
    closes = price_by_ticker.get(r["ticker"])
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
    draws = rng.choice(log_returns, size=(N_SIMULATIONS, n_steps), replace=True)
    mean_draw = draws.mean(axis=0, keepdims=True)
    widen_target = 1 + r["risk_score"] * RISK_WIDEN_MAX
    shift_per_day = (r["macro_score"] * MACRO_SHIFT_MAX) / n_steps
    adj = mean_draw + (draws - mean_draw) * widen_target + shift_per_day
    adj = np.clip(adj, LOG_CAP_LOW, LOG_CAP_HIGH)
    terminal_returns = np.exp(adj.sum(axis=1)) - 1

    samples.append({
        "terminal_returns": terminal_returns,
        "actual_return": actual_price / start_price - 1,
    })
    if (i + 1) % 500 == 0:
        print(f"  進度 {i+1}/{len(rows)} ({time.time()-t0:.0f}s)")

print(f"有效樣本數：{len(samples)}（跳過{skipped}筆，耗時{time.time()-t0:.0f}s）")

rng_split = np.random.default_rng(RNG_SEED)
idx = rng_split.permutation(len(samples))
n_train = int(len(samples) * TRAIN_FRACTION)
train_samples = [samples[i] for i in idx[:n_train]]
test_samples = [samples[i] for i in idx[n_train:]]
print(f"訓練集：{len(train_samples)}　測試集：{len(test_samples)}")


def pinball_loss(sample_list, tau):
    losses = []
    for s in sample_list:
        q = np.percentile(s["terminal_returns"], tau * 100)
        y = s["actual_return"]
        loss = (y - q) * tau if y >= q else (q - y) * (1 - tau)
        losses.append(loss)
    return float(np.mean(losses))


print()
print("=" * 70)
print("STEP 4：對每個候選分位數τ，各自算平均Pinball Loss")
print("=" * 70)
print(f"{'τ':>6} {'訓練PinballLoss':>16} {'測試PinballLoss':>16}")
pinball_by_tau = {}
for tau in QUANTILES:
    train_pl = pinball_loss(train_samples, tau)
    test_pl = pinball_loss(test_samples, tau)
    pinball_by_tau[tau] = (train_pl, test_pl)
    print(f"{tau:>6.2f} {train_pl:>16.4f} {test_pl:>16.4f}")

print()
print("=" * 70)
print("STEP 5：組成對稱候選區間(τ_low, τ_high=1-τ_low)，比較「總Pinball Loss」")
print("=" * 70)
print("總Pinball Loss = 低端Loss + 高端Loss，不像Interval Score有α縮放，")
print("是在同一把尺上，理論上可以直接跨涵蓋率比較數值大小。")
print()
print(f"{'涵蓋率':>8} {'τ_low':>6} {'τ_high':>7} {'訓練總Loss':>11} {'測試總Loss':>11}")
coverage_candidates = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
for coverage in coverage_candidates:
    tau_low = round((1 - coverage) / 2, 2)
    tau_high = round(1 - tau_low, 2)
    train_total = pinball_by_tau[tau_low][0] + pinball_by_tau[tau_high][0]
    test_total = pinball_by_tau[tau_low][1] + pinball_by_tau[tau_high][1]
    print(f"{coverage*100:>7.0f}% {tau_low:>6.2f} {tau_high:>7.2f} {train_total:>11.4f} {test_total:>11.4f}")
