from typing import List, Dict, Any
import pandas as pd
from django.db import connections

from .holdings_calc import compute_structural_metrics, compute_overlap_matrix


DB = "etfdb"


def fetch_latest_metrics_date() -> Any:
    with connections[DB].cursor() as cur:
        cur.execute("SELECT MAX(as_of_date) FROM etf_metrics_daily;")
        return cur.fetchone()[0]


def fetch_metrics(symbols: List[str], as_of_date) -> pd.DataFrame:
    query = """
        SELECT
            m.symbol,
            master.name,
            master.exchange,
            master.tracking_index_name,
            master.distribution_policy,
            m.total_return_1y,
            m.max_drawdown_1y,
            m.sharpe_1y,
            m.volatility_1y,
            m.annualized_yield,
            m.days_to_fill_last_distribution
        FROM etf_metrics_daily m
        JOIN etf_master master
          ON m.symbol = master.symbol
         AND m.exchange = master.exchange
        WHERE m.symbol = ANY(%s)
          AND m.as_of_date = %s
        ORDER BY m.symbol;
    """
    return pd.read_sql(query, connections[DB], params=(symbols, as_of_date))


def fetch_latest_holdings_date(symbols: List[str]):
    query = """
        SELECT MAX(snapshot_date)
        FROM etf_holdings_snapshot
        WHERE symbol = ANY(%s)
          AND snapshot_type = 'holdings';
    """
    with connections[DB].cursor() as cur:
        cur.execute(query, (symbols,))
        return cur.fetchone()[0]


def fetch_holdings_items(symbols: List[str], snapshot_date) -> pd.DataFrame:
    query = """
        SELECT
          s.symbol,
          i.asset_code,
          i.asset_name,
          i.weight,
          i.sector
        FROM etf_holdings_snapshot s
        JOIN etf_holdings_snapshot_items i
          ON s.snapshot_id = i.snapshot_id
        WHERE s.symbol = ANY(%s)
          AND s.snapshot_type = 'holdings'
          AND s.snapshot_date = %s;
    """
    return pd.read_sql(query, connections[DB], params=(symbols, snapshot_date))


def compare_etfs(symbols: List[str]) -> Dict[str, Any]:
    if not symbols:
        return {"error": "No symbols provided"}

    metrics_date = fetch_latest_metrics_date()
    if not metrics_date:
        return {"error": "No metrics data found"}

    metrics_df = fetch_metrics(symbols, metrics_date)
    if metrics_df.empty:
        return {"error": "No metrics data found for given symbols"}

    holdings_date = fetch_latest_holdings_date(symbols)
    if not holdings_date:
        return {
            "metrics_date": str(metrics_date),
            "holdings_date": None,
            "data": metrics_df.to_dict(orient="records"),
            "overlap_matrix": {},
            "note": "No holdings snapshot found for these symbols",
        }

    items_df = fetch_holdings_items(symbols, holdings_date)
    if items_df.empty:
        return {
            "metrics_date": str(metrics_date),
            "holdings_date": str(holdings_date),
            "data": metrics_df.to_dict(orient="records"),
            "overlap_matrix": {},
            "note": "Holdings snapshot exists but no items found",
        }

    structural_df = compute_structural_metrics(items_df)
    combined = metrics_df.merge(structural_df, on="symbol", how="left")

    overlap_matrix = compute_overlap_matrix(items_df, symbols)

    return {
        "metrics_date": str(metrics_date),
        "holdings_date": str(holdings_date),
        "data": combined.to_dict(orient="records"),
        "overlap_matrix": overlap_matrix,
    }