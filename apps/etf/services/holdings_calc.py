from typing import List, Dict
import pandas as pd


def compute_structural_metrics(items_df: pd.DataFrame) -> pd.DataFrame:
    results = []

    if items_df is None or items_df.empty:
        return pd.DataFrame(results)

    for symbol, g in items_df.groupby("symbol"):
        if g.empty:
            continue

        g = g.copy()
        g["weight"] = pd.to_numeric(g["weight"], errors="coerce")
        g = g.dropna(subset=["weight"])

        if g.empty:
            continue

        g["sector"] = g["sector"].fillna("未知").replace("", "未知")

        holding_count = int(g["asset_code"].nunique())

        top10_weight = float(
            g.sort_values("weight", ascending=False)
             .head(10)["weight"]
             .sum()
        )

        g_sorted = g.sort_values("weight", ascending=False)
        largest_row = g_sorted.iloc[0]
        largest_name = largest_row.get("asset_name")
        largest_weight = float(largest_row["weight"])

        sector_sum = (
            g.groupby("sector")["weight"]
             .sum()
             .sort_values(ascending=False)
        )

        top3_sectors = [
            {"sector": sec, "weight": float(w)}
            for sec, w in sector_sum.head(3).items()
        ]

        results.append({
            "symbol": symbol,
            "holding_count": holding_count,
            "top10_weight": top10_weight,
            "largest_holding": largest_name,
            "largest_weight": largest_weight,
            "top3_sectors": top3_sectors,
        })

    return pd.DataFrame(results)


def compute_overlap_matrix(items_df: pd.DataFrame, symbols: List[str]) -> Dict[str, Dict[str, float]]:
    if items_df is None or items_df.empty:
        return {s: {t: (1.0 if s == t else 0.0) for t in symbols} for s in symbols}

    # weight 轉 numeric，避免 Decimal/None
    df = items_df.copy()
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
    df = df.dropna(subset=["weight"])

    maps = {}
    for sym, g in df.groupby("symbol"):
        maps[sym] = dict(zip(g["asset_code"], g["weight"]))

    overlap: Dict[str, Dict[str, float]] = {}
    for a in symbols:
        overlap[a] = {}
        for b in symbols:
            if a == b:
                overlap[a][b] = 1.0
                continue

            ma = maps.get(a, {})
            mb = maps.get(b, {})

            common = set(ma.keys()) & set(mb.keys())
            value = sum(min(ma[c], mb[c]) for c in common)
            overlap[a][b] = float(value)

    return overlap