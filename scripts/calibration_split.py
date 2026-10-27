# scripts/calibration_split.py
"""
調幅校準的時間切分驗證(2026-10-28)：用前半段時間的回測資料算校準比例，在後半段比較預測誤差，
看「乘校準比例」是不是只是在同一批資料上看起來比較好。

資料：scripts/backtest_thesis_scenarios.py 輸出的 out/thesis_backtest_v2.json 的 financial 部分
(每一筆 = 一個情境 × 一個參數：實際一年後比發布當時多變化多少 actual，以及原本的調幅 step)。
只涵蓋營收成長、毛利率、費用率；估值(本益比)與財務風險不在這份資料裡。

比較三種預測(都是預測「這個情境讓參數比發布當時多變化多少」)：
  原本：調幅 = 一般波動幅度
  校準：調幅 = 一般波動幅度 × 前半段算出的校準比例
  不調：0(等於都不勾)
指標：後半段 |額外變化 − 預測| 的中位數。額外變化 = 實際變化 − 後半段對照組(所有論點)的中位數，
先扣掉這段期間所有公司共同的變化(多頭時營收普遍成長)，不然調得越大、越偏多的預測，誤差會被市場整體走勢「幫忙」變小。

2026-10-28 結果：原本 18.4、校準 7.0、統一乘 0.12 6.5、不調 6.2 個百分點——原本的調幅明顯太大，縮小後誤差少六成；
但「不調」最好、各類別的比例在前後半段也不穩定(需求增加在前半段方向相反)，代表情境對營收、毛利率「大小」的預測力很弱，
校準後的調幅應該當成「不誇大的參考值」，不是預測。

用法：python scripts/calibration_split.py
"""
import json
import statistics as st
from collections import defaultdict

MIN_N = 20


def factors(records: list) -> tuple[dict, float]:
    """(情境, 參數) → 校準比例；規則同 thesis_valuation.CALIBRATION：樣本夠、方向一致才採用，其他用可靠比例的中位數。"""
    ctrl = {}
    by_lever = defaultdict(dict)
    for r in records:   # 對照組：同一批(股票, 發布日)的實際變化，每組只算一次
        by_lever[r["lever"]][(r["ticker"], r["as_of"])] = r["actual"]
    for lever, d in by_lever.items():
        ctrl[lever] = st.median(d.values())
    groups = defaultdict(list)
    for r in records:
        groups[(r["type"], r["lever"], r["direction"])].append(r)
    out = {}
    for (t, lever, direction), rs in groups.items():
        steps = [r["step"] for r in rs if r["step"]]
        if len(rs) < MIN_N or not steps:
            continue
        excess = st.median(r["actual"] for r in rs) - ctrl[lever]
        step = st.median(steps)
        if (excess > 0) == (direction > 0) and step:
            out[(t, lever)] = excess / step
    fallback = st.median(out.values()) if out else 0.3
    return out, fallback


def main():
    fin = json.load(open("out/thesis_backtest_v2.json", encoding="utf-8"))["financial"]
    fin = [r for r in fin if r.get("step") is not None]
    cut = st.median(r["as_of"] for r in fin)
    first = [r for r in fin if r["as_of"] < cut]
    second = [r for r in fin if r["as_of"] >= cut]
    print(f"情境×參數 {len(fin)} 筆；切分日 {cut}：前半段 {len(first)} 筆、後半段 {len(second)} 筆")

    f1, fb1 = factors(first)
    print(f"\n前半段算出的校準比例（一般比例 {fb1:.2f}）：")
    for (t, lever), f in sorted(f1.items()):
        print(f"  {t:20} {lever:15} {f:5.2f}")

    by_lever = defaultdict(dict)
    for r in second:
        by_lever[r["lever"]][(r["ticker"], r["as_of"])] = r["actual"]
    ctrl = {lever: st.median(d.values()) for lever, d in by_lever.items()}

    def excess(r):
        return r["actual"] - ctrl[r["lever"]]

    def err(pred):
        return {lever: st.median(abs(excess(r) - pred(r)) for r in second if r["lever"] == lever) * 100
                for lever in sorted({r["lever"] for r in second})} | \
               {"全部": st.median(abs(excess(r) - pred(r)) for r in second) * 100}

    models = {
        "原本（一般波動幅度）": lambda r: r["step"],
        "校準（× 前半段比例）": lambda r: r["step"] * f1.get((r["type"], r["lever"]), fb1),
        "統一 × 前半段一般比例": lambda r: r["step"] * fb1,
        "不調（都不勾）": lambda r: 0.0,
    }
    print("\n後半段的預測誤差中位數（百分點，越小越好）：")
    results = {k: err(m) for k, m in models.items()}
    levers = list(next(iter(results.values())).keys())
    print(f"  {'':18}" + "".join(f"{lv:>16}" for lv in levers))
    for k, v in results.items():
        print(f"  {k:18}" + "".join(f"{v[lv]:16.2f}" for lv in levers))

    # 方向：校準只改大小、不改方向，所以方向命中跟原本一樣；這裡列出來確認情境本身有沒有資訊
    hits = [(r["actual"] > 0) == (r["direction"] > 0) for r in second]
    print(f"\n後半段方向命中（情境說的方向，實際也往那邊變）：{sum(hits) / len(hits) * 100:.1f}%（n={len(hits)}）")


if __name__ == "__main__":
    main()
