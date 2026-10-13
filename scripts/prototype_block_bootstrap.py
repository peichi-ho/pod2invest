# scripts/prototype_block_bootstrap.py
"""
小規模原型：比較「每天獨立抽樣(現行法)」vs「block bootstrap(區塊抽樣)」，
兩者在完全不套shift/widen補償的情況下，原始涵蓋率誰比較接近目標——
用來驗證「block bootstrap能不能從源頭解決現行法低估不確定性的問題」這個假設。

不寫回任何正式程式碼，只是驗證用的一次性腳本，跟 calibrate_bootstrap_path.py
共用同一套資料撈取邏輯，但縮小規模(抽一部分ticker)換取跑更快。

用法：python scripts/prototype_block_bootstrap.py
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

HISTORY_YEARS = 5
MIN_HISTORY_DAYS = 250
N_SIMULATIONS = 500
RNG_SEED = 42
BLOCK_LENGTHS = [5, 10, 20]  # 交易日，分別約是1週/2週/1個月
MAX_TICKERS = 40  # 原型只抽部分股票，換速度

print("=" * 70)
print("STEP 1：撈取候選紀錄(跟calibrate_bootstrap_path.py同一套邏輯)")
print("=" * 70)

all_scored = list(
    StockSentimentScore.objects.using("summariesdb")
    .filter(base__isnull=False, annual_vol__isnull=False)
    .select_related("summary")
    .values("id", "summary_id", "ticker", "summary__published_at")
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

tickers_all = sorted(set(r["ticker"] for r in rows))
rng_pick = np.random.default_rng(RNG_SEED)
tickers = sorted(rng_pick.choice(tickers_all, size=min(MAX_TICKERS, len(tickers_all)), replace=False))
rows = [r for r in rows if r["ticker"] in set(tickers)]
print(f"原型抽樣：{len(tickers)}檔股票，{len(rows)}筆候選紀錄")


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
from concurrent.futures import ThreadPoolExecutor, as_completed
price_by_ticker = {}
t0 = time.time()
with ThreadPoolExecutor(max_workers=10) as pool:
    futures = {pool.submit(fetch_ticker_history, t): t for t in tickers}
    for fut in as_completed(futures):
        ticker, closes, err = fut.result()
        if closes is not None:
            price_by_ticker[ticker] = closes
print(f"成功抓到 {len(price_by_ticker)}/{len(tickers)} 檔 ({time.time()-t0:.0f}s)")


def nearest_close(closes, target, max_lookback=10):
    for back in range(max_lookback + 1):
        d = target - timedelta(days=back)
        if d in closes:
            return closes[d]
    return None


def block_bootstrap_draws(log_returns, n_steps, n_sims, block_len, rng):
    """每次隨機選一個起始index，取連續block_len天，接到還沒滿n_steps為止。"""
    L = len(log_returns)
    out = np.empty((n_sims, n_steps))
    for s in range(n_sims):
        filled = 0
        segs = []
        while filled < n_steps:
            start_idx = rng.integers(0, L - block_len + 1) if L > block_len else 0
            seg = log_returns[start_idx:start_idx + block_len]
            segs.append(seg)
            filled += len(seg)
        out[s] = np.concatenate(segs)[:n_steps]
    return out


print()
print("=" * 70)
print("STEP 3：對每一筆樣本，分別用「逐日獨立抽樣」跟「block bootstrap」跑模擬")
print("=" * 70)

results = {"daily": []}
for bl in BLOCK_LENGTHS:
    results[f"block{bl}"] = []

skipped = 0
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
    actual_return = actual_price / start_price - 1

    rng = np.random.default_rng(RNG_SEED + r["id"])
    draws = rng.choice(log_returns, size=(N_SIMULATIONS, n_steps), replace=True)
    terminal = np.exp(draws.sum(axis=1)) - 1
    results["daily"].append((terminal, actual_return))

    for bl in BLOCK_LENGTHS:
        rng_b = np.random.default_rng(RNG_SEED + r["id"] + bl * 100000)
        draws_b = block_bootstrap_draws(log_returns, n_steps, N_SIMULATIONS, bl, rng_b)
        terminal_b = np.exp(draws_b.sum(axis=1)) - 1
        results[f"block{bl}"].append((terminal_b, actual_return))

    if (i + 1) % 200 == 0:
        print(f"  進度 {i+1}/{len(rows)}")

print(f"有效樣本數：{len(results['daily'])}（跳過 {skipped} 筆）")


def coverage_and_width(samples, low_pct, high_pct):
    hits = 0
    widths = []
    for terminal, actual in samples:
        lo, hi = np.percentile(terminal, low_pct), np.percentile(terminal, high_pct)
        widths.append(hi - lo)
        if lo <= actual <= hi:
            hits += 1
    return hits / len(samples) * 100, np.mean(widths) * 100


print()
print("=" * 70)
print("STEP 4：比較「原始(未套shift/widen)涵蓋率」vs 目標80%，逐日 vs 各種block長度")
print("=" * 70)
for method, samples in results.items():
    cov80, width80 = coverage_and_width(samples, 10, 90)
    cov40, width40 = coverage_and_width(samples, 30, 70)
    print(f"{method:12s}  P10~P90實際涵蓋率={cov80:5.1f}%(寬度{width80:5.1f}%)   "
          f"P30~P70實際涵蓋率={cov40:5.1f}%(寬度{width40:5.1f}%)")
