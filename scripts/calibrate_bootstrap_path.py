# scripts/calibrate_bootstrap_path.py
"""
B方案(bootstrap_path_simulator)參數校準腳本——2026-09-30 第二輪。

跟第一輪校準(已寫進 bootstrap_path_simulator.py 的docstring/註解，2064筆、
訓練/測試80.3%/80.5%)的差異，是回應使用者讀完一份討論校準方法論的對話記錄後
提出的兩個加強：

  1. 評分方法從「最小化 |涵蓋率-80%|」換成正式的 Interval Score
     IS = (U-L) + (2/α)(L-y)·I(y<L) + (2/α)(y-U)·I(y>U)，α = 1-目標涵蓋率。
     同時考慮「涵蓋率夠不夠」跟「區間會不會太寬」，不用再自己發明懲罰係數。

  2. 目標涵蓋率本身當成超參數，跑一輪 30/40/50/60/70/80/90% 全部比較，
     不是一開始就假設80%是對的——但不同涵蓋率的IS用的α不同、懲罰尺度不一樣，
     不能直接比較「哪個涵蓋率的IS最低」，只能在「同一個涵蓋率內」比較不同
     (MACRO_SHIFT_MAX, RISK_WIDEN_MAX)組合的優劣，涵蓋率之間則分開看
     「實際涵蓋率有沒有接近目標」+「區間寬度」兩個指標。

資料來源：StockSentimentScore(summariesdb)——「某一集節目對某一支股票的
risk_score/macro_score判斷」，投資期間改用 BacktestingRecord(該集對該股票論述
的實際起訖時間，跟 apps/calculator/preview_views.py 的 EpisodeListPreviewAPIView
算 default_months 用同一個(summary_id,ticker)配對邏輯) 換算出的月數，不是固定
12個月——第一版重跑時發現：假設固定12個月只能抓到6016筆候選，用真實論述期間
配對後篩出「已到期」的候選只剩1915~1643筆，明顯比較接近原本文件記載的2064筆，
所以推斷原始校準用的就是這個「各自的真實期間」，不是統一12個月。

跟正式的 run_bootstrap_path_simulation() 用同一套邏輯(抽樣、shift/widen調整
公式一模一樣)，這裡只是為了跑網格搜尋，拆成「先算一次未調整的終點報酬分布，
之後對同一份分布套用不同的shift/widen參數」，避免重複跑蒙地卡羅模擬，
如果分開重跑會慢一個數量級以上。

用法：python manage.py shell < scripts/calibrate_bootstrap_path.py
或   python scripts/calibrate_bootstrap_path.py (腳本自己會 django.setup())
"""
import os
import sys
import time
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import numpy as np
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.utils import timezone
from apps.summaries.models import StockSentimentScore, BacktestingRecord

HISTORY_YEARS = 5
MIN_HISTORY_DAYS = 250
N_SIMULATIONS = 500  # 網格搜尋用，降低到500(正式系統預設1000)換取速度，結果穩定性足夠
TRAIN_FRACTION = 0.7
RNG_SEED = 42

MACRO_GRID = [0.02, 0.05, 0.08, 0.12, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.65, 0.80]
RISK_GRID = [0.2, 0.5, 0.8, 1.0, 1.3, 1.6, 2.0, 2.5, 3.0, 4.0]
COVERAGE_LEVELS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

print("=" * 70)
print("STEP 1/4：撈取候選紀錄 (StockSentimentScore配對BacktestingRecord的真實論述期間)")
print("=" * 70)

all_scored = list(
    StockSentimentScore.objects.using("summariesdb")
    .filter(base__isnull=False, annual_vol__isnull=False)
    .select_related("summary")
    .values("id", "summary_id", "ticker", "risk_score", "macro_score", "summary__published_at")
)
print(f"有base/annual_vol的候選(未篩期間)：{len(all_scored)}")

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
        continue  # 這一集這支股票的論述期間還沒走完，沒有真實後續報酬可以核對
    r["horizon_months"] = months
    rows.append(r)

print(f"有真實論述期間(BacktestingRecord)且已到期的候選紀錄數：{len(rows)}")

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
    # 過濾非正值收盤價(例如CL=F原油期貨2020/4曾出現負值)，避免log(負數)=NaN汙染整條報酬池
    closes = {ts.date(): float(v) for ts, v in hist.items() if v > 0}
    return ticker, closes, None


print()
print("=" * 70)
print("STEP 2/4：抓取每檔股票的歷史股價 (單一ticker只抓一次，多執行緒平行)")
print("=" * 70)
t0 = time.time()
price_by_ticker = {}
failed_tickers = []
with ThreadPoolExecutor(max_workers=10) as pool:
    futures = {pool.submit(fetch_ticker_history, t): t for t in tickers}
    done = 0
    for fut in as_completed(futures):
        ticker, closes, err = fut.result()
        done += 1
        if closes is None:
            failed_tickers.append((ticker, err))
        else:
            price_by_ticker[ticker] = closes
        if done % 50 == 0 or done == len(tickers):
            print(f"  進度 {done}/{len(tickers)} ({time.time()-t0:.0f}s)")

print(f"成功：{len(price_by_ticker)}，失敗：{len(failed_tickers)}")
if failed_tickers:
    print("失敗清單(前10):", failed_tickers[:10])


def nearest_close(closes: dict, target, max_lookback=10):
    for back in range(max_lookback + 1):
        d = target - timedelta(days=back)
        if d in closes:
            return closes[d]
    return None


print()
print("=" * 70)
print("STEP 3/4：組出每筆紀錄的(起始價,歷史報酬池,實際論述期間後股價)，並跑一次未調整模擬")
print("=" * 70)

samples = []  # 每筆: dict(raw_terminal_returns=array, start_price, actual_price, risk_score, macro_score)
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

    rng = np.random.default_rng(RNG_SEED + r["id"])
    draws = rng.choice(log_returns, size=(N_SIMULATIONS, n_steps), replace=True)
    cum = draws.sum(axis=1)
    raw_terminal_returns = np.exp(cum) - 1  # 未套shift/widen前的原始終點報酬分布

    samples.append({
        "raw_terminal_returns": raw_terminal_returns,
        "start_price": start_price,
        "actual_price": actual_price,
        "risk_score": r["risk_score"],
        "macro_score": r["macro_score"],
    })
    if (i + 1) % 1000 == 0:
        print(f"  進度 {i+1}/{len(rows)}")

print(f"有效樣本數：{len(samples)}（跳過 {skipped} 筆：股價抓不到/歷史不足）")

rng_split = np.random.default_rng(RNG_SEED)
idx = rng_split.permutation(len(samples))
n_train = int(len(samples) * TRAIN_FRACTION)
train_idx, test_idx = idx[:n_train], idx[n_train:]
train_samples = [samples[i] for i in train_idx]
test_samples = [samples[i] for i in test_idx]
print(f"訓練集：{len(train_samples)}　測試集：{len(test_samples)}")


def evaluate(sample_list, macro_max, risk_max, coverage):
    """回傳 (實際涵蓋率, 平均相對寬度%, 平均Interval Score)"""
    alpha = 1 - coverage
    low_pct = (1 - coverage) / 2 * 100
    high_pct = 100 - low_pct
    hits = 0
    widths = []
    scores = []
    for s in sample_list:
        raw = s["raw_terminal_returns"]
        mean_r = raw.mean()
        shift = s["macro_score"] * macro_max
        widen = 1 + s["risk_score"] * risk_max
        adjusted = mean_r + (raw - mean_r) * widen + shift
        # 全部用「相對報酬率」尺度算(不是絕對股價金額)：不然900元的台積電跟5元的
        # 小型股，同樣1個標準差的相對誤差，絕對金額差一百倍以上，平均IS會被少數
        # 高股價/高波動股票用絕對金額主導，逼網格搜尋一路衝向「越寬越好」(因為
        # 加寬對那幾檔的巨大絕對金額懲罰降幅，遠超過對其他股票增加的絕對寬度成本)。
        L_r = np.percentile(adjusted, low_pct)
        U_r = np.percentile(adjusted, high_pct)
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
    # nan安全：理論上過濾過非正值收盤價後不該再出現NaN，但保留防呆，避免單一
    # 髒樣本讓整組平均值(進而讓網格搜尋比較)靜默壞掉。
    # 額外回傳中位數IS：如果mean跟median差很多，代表少數極端報酬的股票(例如
    # 真的一年漲5倍)還是在主導平均值，用中位數當更穩健的參考。
    return (
        hits / n * 100,
        float(np.nanmean(widths)),
        float(np.nanmean(scores)),
        float(np.nanmedian(scores)),
    )


def crps_from_sorted(sorted_x: np.ndarray, y: float) -> float:
    """
    樣本CRPS：CRPS(F,y) = E|X-y| - 0.5*E|X-X'|，X,X'為F的獨立樣本。
    第二項用排序後O(N log N)公式算(不用真的兩兩配對算O(N^2))：
    ΣΣ|Xi-Xj| = 2*Σ_{i=1}^N (2i-N-1)*X_(i)  (X_(i)為由小到大排序、i從1開始)
    """
    n = len(sorted_x)
    term1 = np.mean(np.abs(sorted_x - y))
    i = np.arange(1, n + 1)
    term2 = np.sum((2 * i - n - 1) * sorted_x) / (n * n)
    return float(term1 - term2)


def evaluate_crps(sample_list, macro_max, risk_max):
    """
    對「整條模擬分布」評分，不指定涵蓋率——回答的是「這組參數校準出來的分布，
    整體跟真實後續報酬有多接近」，用來找一組不依賴任何特定P低~P高切點的
    最佳參數，之後才用這組參數去看哪個涵蓋率的實際表現最貼近宣稱值。
    """
    crps_values = []
    for s in sample_list:
        raw = s["raw_terminal_returns"]
        mean_r = raw.mean()
        shift = s["macro_score"] * macro_max
        widen = 1 + s["risk_score"] * risk_max
        adjusted = np.sort(mean_r + (raw - mean_r) * widen + shift)
        y_r = s["actual_price"] / s["start_price"] - 1
        crps_values.append(crps_from_sorted(adjusted, y_r))
    return float(np.nanmean(crps_values)), float(np.nanmedian(crps_values))


print()
print("=" * 70)
print("STEP 4/4：對每個目標涵蓋率，網格搜尋最小化訓練集平均Interval Score的參數組合")
print("=" * 70)

results = []
for coverage in COVERAGE_LEVELS:
    best = None
    for macro_max in MACRO_GRID:
        for risk_max in RISK_GRID:
            _, _, train_is, _ = evaluate(train_samples, macro_max, risk_max, coverage)
            if np.isnan(train_is):
                continue
            if best is None or train_is < best[0]:
                best = (train_is, macro_max, risk_max)
    train_is, macro_max, risk_max = best
    train_cov, train_width, _, train_is_median = evaluate(train_samples, macro_max, risk_max, coverage)
    test_cov, test_width, test_is, test_is_median = evaluate(test_samples, macro_max, risk_max, coverage)
    results.append({
        "coverage_target": coverage,
        "macro_max": macro_max,
        "risk_max": risk_max,
        "train_coverage": train_cov,
        "train_width": train_width,
        "train_is": train_is,
        "train_is_median": train_is_median,
        "test_coverage": test_cov,
        "test_width": test_width,
        "test_is": test_is,
        "test_is_median": test_is_median,
    })
    print(
        f"目標涵蓋率={coverage*100:.0f}%  最佳參數=(MACRO_SHIFT_MAX={macro_max}, RISK_WIDEN_MAX={risk_max})  "
        f"訓練涵蓋率={train_cov:.1f}% 寬度={train_width:.1f}% 平均IS={train_is:.4f} 中位數IS={train_is_median:.4f}  "
        f"測試涵蓋率={test_cov:.1f}% 寬度={test_width:.1f}% 平均IS={test_is:.4f} 中位數IS={test_is_median:.4f}"
    )

print()
print("=" * 70)
print("對照：目前正式系統的參數 (MACRO_SHIFT_MAX=0.08, RISK_WIDEN_MAX=1.0) 在80%目標下的表現")
print("=" * 70)
cur_train_cov, cur_train_width, cur_train_is, cur_train_is_median = evaluate(train_samples, 0.08, 1.0, 0.8)
cur_test_cov, cur_test_width, cur_test_is, cur_test_is_median = evaluate(test_samples, 0.08, 1.0, 0.8)
print(
    f"訓練涵蓋率={cur_train_cov:.1f}% 寬度={cur_train_width:.1f}% 平均IS={cur_train_is:.4f} 中位數IS={cur_train_is_median:.4f}  "
    f"測試涵蓋率={cur_test_cov:.1f}% 寬度={cur_test_width:.1f}% 平均IS={cur_test_is:.4f} 中位數IS={cur_test_is_median:.4f}"
)

print()
print("=" * 70)
print("完整結果表格 (可複製)")
print("=" * 70)
header = (
    f"{'目標涵蓋率':>8} {'MACRO_MAX':>10} {'RISK_MAX':>9} {'訓練涵蓋率':>9} {'訓練寬度%':>9} "
    f"{'訓練平均IS':>10} {'訓練中位IS':>10} {'測試涵蓋率':>9} {'測試寬度%':>9} {'測試平均IS':>10} {'測試中位IS':>10}"
)
print(header)
for r in results:
    print(
        f"{r['coverage_target']*100:>7.0f}% {r['macro_max']:>10} {r['risk_max']:>9} "
        f"{r['train_coverage']:>8.1f}% {r['train_width']:>8.1f}% "
        f"{r['train_is']:>10.4f} {r['train_is_median']:>10.4f} "
        f"{r['test_coverage']:>8.1f}% {r['test_width']:>8.1f}% "
        f"{r['test_is']:>10.4f} {r['test_is_median']:>10.4f}"
    )

print()
print("=" * 70)
print("STEP 5/5：用CRPS找「單一一組」最佳參數(不指定涵蓋率，對整條模擬分布評分)")
print("=" * 70)
print("目的：不是要比較哪個涵蓋率的IS最低(不能比)，而是先把模擬分布本身校準到")
print("最準，再用這組參數去看哪個涵蓋率的『實際涵蓋率』最貼近『宣稱的涵蓋率』，")
print("藉此決定P幾~P幾才是真的該顯示的區間。")
print()

t0 = time.time()
best_crps = None
for macro_max in MACRO_GRID:
    for risk_max in RISK_GRID:
        train_crps_mean, _ = evaluate_crps(train_samples, macro_max, risk_max)
        if np.isnan(train_crps_mean):
            continue
        if best_crps is None or train_crps_mean < best_crps[0]:
            best_crps = (train_crps_mean, macro_max, risk_max)
print(f"CRPS網格搜尋耗時 {time.time()-t0:.0f}s")

crps_train_mean, crps_macro_max, crps_risk_max = best_crps
crps_train_mean2, crps_train_median = evaluate_crps(train_samples, crps_macro_max, crps_risk_max)
crps_test_mean, crps_test_median = evaluate_crps(test_samples, crps_macro_max, crps_risk_max)
print(
    f"CRPS最佳參數 = (MACRO_SHIFT_MAX={crps_macro_max}, RISK_WIDEN_MAX={crps_risk_max})　"
    f"訓練CRPS：平均={crps_train_mean2:.4f} 中位數={crps_train_median:.4f}　"
    f"測試CRPS：平均={crps_test_mean:.4f} 中位數={crps_test_median:.4f}"
)

# 對照：目前正式系統參數的CRPS表現
cur_crps_train_mean, cur_crps_train_median = evaluate_crps(train_samples, 0.08, 1.0)
cur_crps_test_mean, cur_crps_test_median = evaluate_crps(test_samples, 0.08, 1.0)
print(
    f"對照(目前正式參數 0.08/1.0)：訓練CRPS 平均={cur_crps_train_mean:.4f} 中位數={cur_crps_train_median:.4f}　"
    f"測試CRPS 平均={cur_crps_test_mean:.4f} 中位數={cur_crps_test_median:.4f}"
)

print()
print("=" * 70)
print(f"用CRPS最佳參數(MACRO_SHIFT_MAX={crps_macro_max}, RISK_WIDEN_MAX={crps_risk_max})，")
print("檢查30~90%每個涵蓋率的『實際涵蓋率 vs 宣稱涵蓋率』貼近程度 + 寬度")
print("=" * 70)
diag_header = f"{'宣稱涵蓋率':>8} {'訓練實際涵蓋率':>12} {'訓練落差':>8} {'訓練寬度%':>9} {'測試實際涵蓋率':>12} {'測試落差':>8} {'測試寬度%':>9}"
print(diag_header)
for coverage in COVERAGE_LEVELS:
    tc, tw, _, _ = evaluate(train_samples, crps_macro_max, crps_risk_max, coverage)
    sc, sw, _, _ = evaluate(test_samples, crps_macro_max, crps_risk_max, coverage)
    print(
        f"{coverage*100:>7.0f}% {tc:>11.1f}% {tc-coverage*100:>+7.1f}pp {tw:>8.1f}% "
        f"{sc:>11.1f}% {sc-coverage*100:>+7.1f}pp {sw:>8.1f}%"
    )
