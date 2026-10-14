"""
市場環境「空頭日占比倍數」的經驗校準（方法A：量實際比例，不跑模擬）。

要回答的問題：
  使用者選「偏空」或「偏多」時，如果那個情境後來真的成真，
  「後面12個月實際的空頭日占比」大約是「抽樣母體(過去5年)的空頭日占比」的幾倍？
  → 這就是 偏空／偏多 預設倍數(目前 1.6 與 0.5)應該對照的數字。

做法：
  1. 對基準指數(台股^TWII、美股^GSPC)，每隔約一個月取一個「模擬基準日」。
  2. S_前：只用基準日以前的資料標記(跟正式功能一樣，含4年脈絡)，算過去5年空頭日占比。
  3. S_後：後面252個交易日的空頭日占比，用整段歷史的20%規則標記(事後標記，校準用，不能拿來預測)。
  4. 依「後面12個月指數實際報酬」把基準日分組(偏空組／偏多組／全部)，看 S_後 ÷ S_前。

注意：
  - 相鄰基準日的前後窗口大幅重疊，樣本不獨立；表裡同時列出涵蓋的年份數當作「獨立事件」的粗略指標。
  - 事後標記看了未來，只用於校準，不能拿來判斷「現在」是不是熊市。
  - 純分析，不改任何現有程式，也不寫資料庫。

用法：
  python scripts/calibrate_regime_multiplier.py
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 直接載入 market_regime.py，避免需要啟動整個 Django
_spec = importlib.util.spec_from_file_location(
    "market_regime", os.path.join(ROOT, "apps", "calculator", "services", "market_regime.py"))
market_regime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(market_regime)

bear_labels = market_regime.bear_labels
CONTEXT_DAYS = int(market_regime.CONTEXT_YEARS * 252)
POOL_DAYS = 5 * 252          # 抽樣母體：過去5年
HORIZON_DAYS = 252           # 模擬期間：12個月
STEP_DAYS = 21               # 約一個月取一個基準日
CURRENT_PRESETS = {"偏空": 1.6, "偏多": 0.5}


def load_close(symbol: str, start: str = "1990-01-01") -> pd.Series:
    import yfinance as yf

    s = yf.Ticker(symbol).history(start=start, interval="1d", auto_adjust=True)["Close"].dropna()
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    return s


def build_rows(close: pd.Series) -> pd.DataFrame:
    px = close.values
    n = len(px)
    full_labels = bear_labels(close).values                      # 事後標記(整段)
    rows = []
    first = POOL_DAYS + CONTEXT_DAYS
    for i in range(first, n - HORIZON_DAYS, STEP_DAYS):
        # 正式功能的標記方式：只看基準日(含)以前的資料
        ctx = close.iloc[i - POOL_DAYS - CONTEXT_DAYS + 1: i + 1]
        lab_prefix = bear_labels(ctx).values
        s_before = float((lab_prefix[-POOL_DAYS:] == 0).mean())
        s_after = float((full_labels[i + 1: i + 1 + HORIZON_DAYS] == 0).mean())
        fwd = float(px[i + HORIZON_DAYS] / px[i] - 1)
        rows.append({"date": close.index[i], "s_before": s_before, "s_after": s_after, "fwd_ret": fwd})
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame, mask, label: str) -> dict:
    d = df[mask]
    if d.empty:
        return {"組別": label, "樣本數": 0}
    sb, sa = d["s_before"].mean(), d["s_after"].mean()
    ok = d[d["s_before"] >= 0.05]
    per = (ok["s_after"] / ok["s_before"]) if len(ok) else pd.Series(dtype=float)
    return {
        "組別": label,
        "樣本數": len(d),
        "涵蓋年份數": d["date"].dt.year.nunique(),
        "S_前平均": round(sb, 3),
        "S_後平均": round(sa, 3),
        "倍數(平均比平均)": round(sa / sb, 2) if sb > 0 else None,
        "倍數(逐筆中位數)": round(float(per.median()), 2) if len(per) else None,
        "S_後P25": round(float(d["s_after"].quantile(0.25)), 2),
        "S_後中位數": round(float(d["s_after"].median()), 2),
        "S_後P75": round(float(d["s_after"].quantile(0.75)), 2),
        "後12月報酬平均": f"{d['fwd_ret'].mean():+.1%}",
    }


def report(name: str, df: pd.DataFrame) -> None:
    print(f"\n{'=' * 78}\n{name}：基準日 {len(df)} 個（{df['date'].min().date()} ~ {df['date'].max().date()}）")
    q20, q80 = df["fwd_ret"].quantile([0.2, 0.8])
    groups = [
        ("全部", df["fwd_ret"].notna()),
        ("偏空：後12個月報酬 ≤ −10%", df["fwd_ret"] <= -0.10),
        ("偏空(深)：後12個月報酬 ≤ −20%", df["fwd_ret"] <= -0.20),
        (f"偏空(後20%分位 ≤ {q20:+.0%})", df["fwd_ret"] <= q20),
        ("偏多：後12個月報酬 ≥ +20%", df["fwd_ret"] >= 0.20),
        (f"偏多(前20%分位 ≥ {q80:+.0%})", df["fwd_ret"] >= q80),
    ]
    out = pd.DataFrame([summarize(df, m, l) for l, m in groups])
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    print(out.to_string(index=False))

    # 乘法假設是否合理：偏空組裡，S_後 會不會其實跟 S_前 無關(接近固定水位)
    bear = df[df["fwd_ret"] <= -0.10]
    if len(bear) >= 12:
        # 很多 S_前 是 0 會讓分位數重複，改用排名再切三等分
        bear = bear.assign(tercile=pd.qcut(bear["s_before"].rank(method="first"), 3, labels=["S_前低", "S_前中", "S_前高"]))
        t = bear.groupby("tercile", observed=True).agg(樣本數=("s_after", "size"), S_前=("s_before", "mean"), S_後=("s_after", "mean")).round(3)
        print("\n偏空組(後12個月 ≤ −10%)依 S_前 三等分：")
        print(t.to_string())
        corr = bear[["s_before", "s_after"]].corr().iloc[0, 1]
        print(f"S_前 與 S_後 的相關係數：{corr:.2f}（越接近0，越代表『倍數』不是好的描述，水位比較像常數）")

    # 現行預設倍數對照
    s_all = df["s_before"].mean()
    for label, mult in CURRENT_PRESETS.items():
        print(f"現行{label}倍數 {mult}：S_前平均 {s_all:.2f} → 預設占比約 {min(max(s_all * mult, 0.05), 0.95):.2f}")


def main() -> None:
    for name, symbol in [("台股加權指數 ^TWII", "^TWII"), ("S&P 500 ^GSPC(1990年起)", "^GSPC")]:
        close = load_close(symbol)
        df = build_rows(close)
        report(name, df)
        out_csv = os.path.join(ROOT, "scripts", f"regime_multiplier_{symbol.strip('^').lower()}.csv")
        df.to_csv(out_csv, index=False)
        print(f"\n(明細已存 {os.path.relpath(out_csv, ROOT)})")


if __name__ == "__main__":
    sys.exit(main())
