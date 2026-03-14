import os
import math
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from datetime import timedelta
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[3]
load_dotenv(BASE_DIR / ".env")

PG_HOST = os.getenv("ETF_DB_HOST", "")
PG_PORT = int(os.getenv("ETF_DB_PORT", "5432"))
PG_DB   = os.getenv("ETF_DB_NAME", "postgres")
PG_USER = os.getenv("ETF_DB_USER", "")
PG_PASS = os.getenv("ETF_DB_PASSWORD", "")

if not all([PG_HOST, PG_DB, PG_USER, PG_PASS]):
    raise RuntimeError("ETF database environment variables are not fully set.")

# 全域變數 EXCHANGES 為 List
EXCHANGES = [x.strip() for x in os.getenv("EXCHANGES", "TWSE,TPEx").split(",")]
MIN_DAYS_REQUIRED = 200   # 至少 200 個交易日才算 1Y 指標

def get_read_engine():
    user = quote_plus(PG_USER)
    pwd  = quote_plus(PG_PASS)
    host = PG_HOST
    port = PG_PORT
    db   = PG_DB
    url = f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{db}"
    return create_engine(url, pool_pre_ping=True)

def get_connection():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS
    )

def get_n_years_ago_trade_date(conn, symbol, exchange, latest_date, years):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT MAX(trade_date)
            FROM etf_prices
            WHERE symbol=%s
              AND exchange=%s
              AND trade_date <= %s::date - (%s || ' years')::interval
        """, (symbol, exchange, latest_date, years))
        return cur.fetchone()[0]

def get_latest_trade_date(conn, exchange):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT MAX(trade_date)
            FROM etf_prices
            WHERE exchange=%s;
        """, (exchange,))
        return cur.fetchone()[0]

# 修正：加入 exchange 參數
def fetch_price_data(engine, symbol, exchange, start_date, end_date):
    query = text("""
        SELECT trade_date, close
        FROM etf_prices
        WHERE symbol = :symbol
          AND exchange = :exchange
          AND trade_date >= :start_date
          AND trade_date <= :end_date
        ORDER BY trade_date;
    """)
    with engine.connect() as conn:
        return pd.read_sql(
            query, conn,
            params={
                "symbol": symbol,
                "exchange": exchange,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

def compute_period_metrics(df, min_days=200):
    if df is None or df.empty or "tri" not in df.columns:
        return None
    if len(df) < min_days:
        return None

    tri = df["tri"].astype(float)
    total_return = tri.iloc[-1] / tri.iloc[0] - 1

    rolling_max = tri.cummax()
    drawdown = tri / rolling_max - 1
    max_drawdown = float(drawdown.min())

    ret = tri.pct_change().dropna()
    if len(ret) < min_days:
        return None

    mean_daily = ret.mean()
    std_daily = ret.std()

    if std_daily == 0 or math.isnan(std_daily):
        sharpe = None
        volatility = None
    else:
        sharpe = float((mean_daily / std_daily) * math.sqrt(252))
        volatility = float(std_daily * math.sqrt(252))

    return (float(total_return), float(max_drawdown), sharpe, volatility)

# 修正：加入 exchange 參數
def apply_splits(conn, symbol, exchange, df):
    if df.empty:
        return df
    with conn.cursor() as cur:
        cur.execute("""
            SELECT split_date, split_ratio
            FROM etf_splits
            WHERE symbol=%s AND exchange=%s
              AND split_date >= %s AND split_date <= %s
            ORDER BY split_date;
        """, (symbol, exchange, df["trade_date"].min(), df["trade_date"].max()))
        splits = cur.fetchall()

    if not splits:
        return df

    df = df.copy()
    for split_date, ratio in splits:
        df.loc[df["trade_date"] < split_date, "close"] = df.loc[df["trade_date"] < split_date, "close"] / float(ratio)
    return df

# 修正：加入 exchange 參數
def fetch_splits(conn, symbol, exchange, start_date, end_date):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT split_date, split_ratio
            FROM etf_splits
            WHERE symbol=%s AND exchange=%s
              AND split_date >= %s AND split_date <= %s
            ORDER BY split_date;
        """, (symbol, exchange, start_date, end_date))
        return [(r[0], float(r[1])) for r in cur.fetchall()]

# 修正：加入 exchange 參數
def fetch_dividends(conn, symbol, exchange, start_date, end_date):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ex_date, cash_amount
            FROM etf_distributions
            WHERE symbol=%s AND exchange=%s
              AND ex_date >= %s AND ex_date <= %s
            ORDER BY ex_date;
        """, (symbol, exchange, start_date, end_date))
        return [(r[0], float(r[1] or 0.0)) for r in cur.fetchall()]

def adjust_dividends_for_splits(dividends, splits):
    if not dividends or not splits:
        return dividends
    out = []
    for ex_date, cash in dividends:
        adj_cash = float(cash)
        for split_date, ratio in splits:
            if ex_date < split_date:
                adj_cash /= ratio
        out.append((ex_date, adj_cash))
    return out

def build_total_return_index(df_prices: pd.DataFrame, dividends):
    df = df_prices.copy()
    if df.empty:
        df["div_cash"] = []
        df["tri"] = []
        return df

    div_map = {}
    for d, cash in dividends:
        div_map[d] = div_map.get(d, 0.0) + float(cash)

    df["div_cash"] = df["trade_date"].map(lambda d: div_map.get(d, 0.0))
    df["tri"] = 1.0

    tri_vals = [1.0]
    closes = df["close"].astype(float).tolist()
    divs = df["div_cash"].astype(float).tolist()

    for i in range(1, len(df)):
        prev_tri = tri_vals[-1]
        prev_p = closes[i-1]
        p = closes[i]
        d = divs[i]
        if prev_p == 0 or math.isnan(prev_p) or math.isnan(p):
            tri_vals.append(prev_tri)
        else:
            tri_vals.append(prev_tri * ((p + d) / prev_p))

    df["tri"] = tri_vals
    return df

def get_adjusted_close_on(conn, engine, symbol, exchange, d):
    df = fetch_price_data(engine, symbol, exchange, d, d)
    if df.empty:
        return None
    df = apply_splits(conn, symbol, exchange, df)
    return float(df["close"].iloc[-1])

def compute_annualized_yield(conn, engine, symbol, exchange, as_of_date):
    close_price = get_adjusted_close_on(conn, engine, symbol, exchange, as_of_date)
    if close_price is None or close_price == 0:
        return None

    start_date = as_of_date - timedelta(days=365)
    splits = fetch_splits(conn, symbol, exchange, start_date, as_of_date)
    dividends = fetch_dividends(conn, symbol, exchange, start_date, as_of_date)
    dividends = adjust_dividends_for_splits(dividends, splits)

    total_div = sum(cash for _, cash in dividends) if dividends else 0.0
    return total_div / close_price

def compute_days_to_fill_last_distribution(conn, engine, symbol, exchange, as_of_date):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ex_date, cash_amount
            FROM etf_distributions
            WHERE symbol=%s AND exchange=%s AND ex_date <= %s
            ORDER BY ex_date DESC
            LIMIT 1
        """, (symbol, exchange, as_of_date))
        row = cur.fetchone()
        if not row:
            return None

        ex_date = row[0]
        splits = fetch_splits(conn, symbol, exchange, ex_date, as_of_date)
        cash_adj = adjust_dividends_for_splits([(ex_date, row[1])], splits)[0][1]
        cash_amount = float(cash_adj)

        cur.execute("""
            SELECT trade_date
            FROM etf_prices
            WHERE symbol=%s AND exchange=%s AND trade_date < %s
            ORDER BY trade_date DESC
            LIMIT 1
        """, (symbol, exchange, ex_date))
        row = cur.fetchone()
        if not row:
            return None
        pre_trade_date = row[0]

    df = fetch_price_data(engine, symbol, exchange, pre_trade_date, as_of_date)
    if df.empty:
        return None
    df = apply_splits(conn, symbol, exchange, df)

    pre_row = df[df["trade_date"] == pre_trade_date]
    if pre_row.empty:
        return None
    pre_close = float(pre_row["close"].iloc[0])

    # 修正：改用「股價是否大於等於除息前收盤價」來判斷填息，並計算交易日天數
    df_after = df[df["trade_date"] >= ex_date]
    trading_days = 1
    for _, r in df_after.iterrows():
        c = float(r["close"])
        if c >= pre_close:
            return trading_days
        trading_days += 1

    return None

def compute_for_exchange(conn, engine, exchange):
    print(f"\n=== Computing metrics for {exchange} ===")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT MAX(trade_date)
            FROM etf_prices
            WHERE exchange=%s
        """, (exchange,))
        latest_date = cur.fetchone()[0]

    if not latest_date:
        print("No price data.")
        return []

    print(f"As of {latest_date}")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT symbol
            FROM etf_master
            WHERE exchange=%s AND status='active'
        """, (exchange,))
        symbols = [r[0] for r in cur.fetchall()]

    results = []

    for symbol in symbols:
        # 1. 改成找出這檔 ETF 最早的交易日 (為了效能，最多往回找 6 年即可)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MIN(trade_date)
                FROM etf_prices
                WHERE symbol=%s AND exchange=%s
                  AND trade_date >= %s::date - interval '6 years'
            """, (symbol, exchange, latest_date))
            min_date = cur.fetchone()[0]

        # 如果連一筆價格都沒有，才真的跳過不處理
        if not min_date:
            continue

        # 2. 用找出的最早日期來抓取價格 DataFrame
        df = fetch_price_data(engine, symbol, exchange, min_date, latest_date)
        if df.empty:
            continue

        # 3. 處理除權息與分割 (這裡維持你原本正確的寫法)
        splits = fetch_splits(conn, symbol, exchange, df["trade_date"].min(), df["trade_date"].max())
        df = apply_splits(conn, symbol, exchange, df)

        dividends = fetch_dividends(conn, symbol, exchange, df["trade_date"].min(), df["trade_date"].max())
        dividends = adjust_dividends_for_splits(dividends, splits)

        df = build_total_return_index(df, dividends)

        metrics = {}
        for years in [1, 3, 5]:
            # 這裡就會發揮作用：如果未滿 3 年，start_n 會是 None，metrics[3] 就會被設為 None
            start_n = get_n_years_ago_trade_date(conn, symbol, exchange, latest_date, years)
            if not start_n:
                metrics[years] = None
                continue

            df_n = df[df["trade_date"] >= start_n]
            metrics[years] = compute_period_metrics(df_n)

        annual_yield = compute_annualized_yield(conn, engine, symbol, exchange, latest_date)
        days_to_fill = compute_days_to_fill_last_distribution(conn, engine, symbol, exchange, latest_date)

        results.append((
            symbol, exchange, latest_date,
            metrics[1][0] if metrics[1] else None,
            metrics[3][0] if metrics[3] else None,
            metrics[5][0] if metrics[5] else None,
            metrics[1][1] if metrics[1] else None,
            metrics[3][1] if metrics[3] else None,
            metrics[5][1] if metrics[5] else None,
            metrics[1][2] if metrics[1] else None,
            metrics[1][3] if metrics[1] else None,
            annual_yield,
            days_to_fill
        ))

    return results

def main():
    conn = None
    engine = None
    try:
        conn = get_connection()
        engine = get_read_engine()

        all_results = []
        for ex in EXCHANGES:
            res = compute_for_exchange(conn, engine, ex)
            all_results.extend(res)

        if not all_results:
            print("No metrics computed.")
            return

        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO etf_metrics_daily(
                    symbol, exchange, as_of_date,
                    total_return_1y, total_return_3y, total_return_5y,
                    max_drawdown_1y, max_drawdown_3y, max_drawdown_5y,
                    sharpe_1y, volatility_1y,
                    annualized_yield, days_to_fill_last_distribution
                )
                VALUES %s
                ON CONFLICT (symbol, exchange, as_of_date)
                DO UPDATE SET
                    total_return_1y = EXCLUDED.total_return_1y,
                    total_return_3y = EXCLUDED.total_return_3y,
                    total_return_5y = EXCLUDED.total_return_5y,
                    max_drawdown_1y = EXCLUDED.max_drawdown_1y,
                    max_drawdown_3y = EXCLUDED.max_drawdown_3y,
                    max_drawdown_5y = EXCLUDED.max_drawdown_5y,
                    sharpe_1y = EXCLUDED.sharpe_1y,
                    volatility_1y = EXCLUDED.volatility_1y,
                    annualized_yield = EXCLUDED.annualized_yield,
                    days_to_fill_last_distribution = EXCLUDED.days_to_fill_last_distribution,
                    computed_at = now();
                """,
                all_results,
                page_size=500
            )

        conn.commit()
        print(f"Inserted/Updated {len(all_results)} ETF metrics.")

    except Exception:
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()
        if engine:
            engine.dispose()

if __name__ == "__main__":
    main()