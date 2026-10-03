# scripts/prototype_longer_window_fat_tail.py
"""
延續 prototype_block_bootstrap.py 的原型測試——block bootstrap沒有改善涵蓋率之後，
換測試另外兩個方向，同一批40檔股票/約200筆樣本，方便直接比較：

  1. 拉長歷史窗口(HISTORY_YEARS: 5→8→10年)：假設是歷史涵蓋期越長，越有機會
     抽到真正的極端事件(例如2020疫情崩盤、2022升息熊市)，讓抽樣母體本身就有
     比較肥的尾部，不用事後人工加寬。
  2. 人工加粗尾部(tail oversampling)：抽樣時，有一定機率(TAIL_PROB)故意只從
     歷史報酬裡最極端的一小撮(TAIL_PCT)去抽，其餘機率才照常從全部歷史抽——
     這樣抽出來的極端值還是「真實發生過的」，不是憑空捏造或線性放大，只是
     刻意調高極端值被抽到的機率。

用法：python scripts/prototype_longer_window_fat_tail.py
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

MIN_HISTORY_DAYS = 250
N_SIMULATIONS = 500
RNG_SEED = 42
MAX_TICKERS = 40
HISTORY_YEARS_OPTIONS = [5, 8, 10]
TAIL_VARIANTS = [(0.10, 0.15), (0.10, 0.30), (0.05, 0.30)]  # (TAIL_PCT, TAIL_PROB)

print("=" * 70)
print("STEP 1：撈取候選紀錄(跟上一輪原型同一套亂數種子，抽到同一批股票)")
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


def fetch_ticker_history(ticker: str, years_back: int):
    import yfinance as yf
    ticker_rows = [r for r in rows if r["ticker"] == ticker]
    dates = [r["summary__published_at"].date() for r in ticker_rows]
    max_horizon_days = max(r["horizon_months"] for r in ticker_rows) * 31 + 10
    start = min(dates) - timedelta(days=int(years_back * 365.25) + 10)
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
print("STEP 2：抓歷史股價(抓最長10年份，之後各窗口長度從同一份資料裡切)")
print("=" * 70)
price_by_ticker = {}
t0 = time.time()
with ThreadPoolExecutor(max_workers=10) as pool:
    futures = {pool.submit(fetch_ticker_history, t, max(HISTORY_YEARS_OPTIONS)): t for t in tickers}
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


def coverage_and_width(samples, low_pct, high_pct):
    hits, widths = 0, []
    for terminal, actual in samples:
        lo, hi = np.percentile(terminal, low_pct), np.percentile(terminal, high_pct)
        widths.append(hi - lo)
        if lo <= actual <= hi:
            hits += 1
    return hits / len(samples) * 100, np.mean(widths) * 100


def build_sample_common(r, closes, years_back):
    asof = r["summary__published_at"].date()
    months = r["horizon_months"]
    n_steps = max(1, round(months / 12 * 252))
    start_price = nearest_close(closes, asof)
    target_date = asof + timedelta(days=months * 30)
    actual_price = nearest_close(closes, target_date)
    if start_price is None or actual_price is None:
        return None
    window_start = asof - timedelta(days=int(years_back * 365.25))
    hist_dates = sorted(d for d in closes if window_start <= d <= asof)
    if len(hist_dates) < MIN_HISTORY_DAYS:
        return None
    hist_prices = np.array([closes[d] for d in hist_dates])
    log_returns = np.diff(np.log(hist_prices))
    actual_return = actual_price / start_price - 1
    return n_steps, log_returns, actual_return


print()
print("=" * 70)
print("STEP 3：方向1，測試不同歷史窗口長度(5/8/10年)")
print("=" * 70)
for years_back in HISTORY_YEARS_OPTIONS:
    samples = []
    skipped = 0
    for r in rows:
        closes = price_by_ticker.get(r["ticker"])
        if closes is None:
            skipped += 1
            continue
        built = build_sample_common(r, closes, years_back)
        if built is None:
            skipped += 1
            continue
        n_steps, log_returns, actual_return = built
        rng = np.random.default_rng(RNG_SEED + r["id"])
        draws = rng.choice(log_returns, size=(N_SIMULATIONS, n_steps), replace=True)
        terminal = np.exp(draws.sum(axis=1)) - 1
        samples.append((terminal, actual_return))
    cov80, w80 = coverage_and_width(samples, 10, 90)
    cov40, w40 = coverage_and_width(samples, 30, 70)
    print(f"HISTORY_YEARS={years_back:2d}  有效樣本{len(samples):4d}(跳過{skipped:3d})  "
          f"P10~P90涵蓋率={cov80:5.1f}%(寬{w80:5.1f}%)  P30~P70涵蓋率={cov40:5.1f}%(寬{w40:5.1f}%)")

print()
print("=" * 70)
print("STEP 4：方向2，人工加粗尾部(固定用5年窗口，只改抽樣機率)")
print("=" * 70)


def tail_oversample_draws(log_returns, n_steps, n_sims, tail_pct, tail_prob, rng):
    abs_ret = np.abs(log_returns)
    cutoff = np.percentile(abs_ret, 100 * (1 - tail_pct))
    tail_pool = log_returns[abs_ret >= cutoff]
    if len(tail_pool) == 0:
        tail_pool = log_returns
    out = np.empty((n_sims, n_steps))
    for s in range(n_sims):
        use_tail = rng.random(n_steps) < tail_prob
        n_tail = use_tail.sum()
        vals = np.empty(n_steps)
        vals[use_tail] = rng.choice(tail_pool, size=n_tail, replace=True)
        vals[~use_tail] = rng.choice(log_returns, size=n_steps - n_tail, replace=True)
        out[s] = vals
    return out


for tail_pct, tail_prob in TAIL_VARIANTS:
    samples = []
    for r in rows:
        closes = price_by_ticker.get(r["ticker"])
        if closes is None:
            continue
        built = build_sample_common(r, closes, 5)
        if built is None:
            continue
        n_steps, log_returns, actual_return = built
        rng = np.random.default_rng(RNG_SEED + r["id"] + int(tail_pct * 1000) + int(tail_prob * 100000))
        draws = tail_oversample_draws(log_returns, n_steps, N_SIMULATIONS, tail_pct, tail_prob, rng)
        terminal = np.exp(draws.sum(axis=1)) - 1
        samples.append((terminal, actual_return))
    cov80, w80 = coverage_and_width(samples, 10, 90)
    cov40, w40 = coverage_and_width(samples, 30, 70)
    print(f"tail_pct={tail_pct:.2f} tail_prob={tail_prob:.2f}  有效樣本{len(samples):4d}  "
          f"P10~P90涵蓋率={cov80:5.1f}%(寬{w80:5.1f}%)  P30~P70涵蓋率={cov40:5.1f}%(寬{w40:5.1f}%)")

print()
print("對照組(基準)：HISTORY_YEARS=5, 逐日無加粗  P10~P90=65.6%(寬94.6%)  P30~P70=33.0%(寬37.1%)")
