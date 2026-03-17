import os
import re
import json
import certifi
import datetime as dt
import requests
import psycopg2
from psycopg2.extras import execute_values

from pathlib import Path
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

# 單一交易所：TWSE 或 TPEx
EXCHANGES = [x.strip() for x in os.getenv("EXCHANGES", "TWSE,TPEx").split(",") if x.strip()]
DATE_YYYYMMDD = os.getenv("DATE")  # e.g. 20251231; if None -> today

SOURCE_TWSE = "TWSE"
SOURCE_TPEX = "TPEX_WEB"

TPEX_URL = "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php"


# ==============================
# Common helpers
# ==============================
def parse_date_yyyymmdd(s: str) -> dt.date:
    return dt.date(int(s[0:4]), int(s[4:6]), int(s[6:8]))


def ensure_month_partition(cur, d: dt.date):
    start = d.replace(day=1)
    if start.month == 12:
        end = dt.date(start.year + 1, 1, 1)
    else:
        end = dt.date(start.year, start.month + 1, 1)

    part_name = f"etf_prices_{start.year}_{start.month:02d}"
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {part_name} PARTITION OF etf_prices
    FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}');
    """)


def to_num(x):
    if x is None:
        return None
    s = str(x).strip().replace(",", "")
    if s in ("", "--", "—", "-"):
        return None
    try:
        return float(s)
    except:
        return None


def to_int(x):
    v = to_num(x)
    return int(v) if v is not None else None


def fetch_etf_master_symbols_with_inception(conn, exchange: str):
    """
    returns dict[symbol_upper] = inception_date
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, inception_date
            FROM etf_master
            WHERE exchange=%s AND status='active'
            """,
            (exchange,)
        )
        out = {}
        for sym, inc in cur.fetchall():
            if not sym:
                continue
            out[str(sym).replace("\u3000", "").strip().upper()] = inc
        return out


def fetch_written_symbols(conn, exchange: str, trade_date: dt.date) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol
            FROM etf_prices
            WHERE exchange=%s AND trade_date=%s
            """,
            (exchange, trade_date)
        )
        return {str(r[0]).replace("\u3000", "").strip().upper() for r in cur.fetchall() if r[0]}


UPSERT_SQL = """
INSERT INTO etf_prices(symbol, exchange, trade_date, open, high, low, close, volume, source)
VALUES %s
ON CONFLICT (symbol, exchange, trade_date) DO UPDATE SET
  open=EXCLUDED.open,
  high=EXCLUDED.high,
  low=EXCLUDED.low,
  close=EXCLUDED.close,
  volume=EXCLUDED.volume,
  source=EXCLUDED.source,
  retrieved_at=now()
"""


# ==============================
# TWSE fetch/parse
# ==============================
def fetch_twse_mi_index(date_yyyymmdd: str) -> dict:
    url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={date_yyyymmdd}&type=ALL"
    r = requests.get(url, timeout=30, verify=certifi.where())
    r.raise_for_status()
    return r.json()


def pick_twse_daily_close_table(payload: dict):
    tables = payload.get("tables") or []
    if not tables:
        print("[TWSE] No tables returned (可能是非交易日)")
        return None

    for t in tables:
        title = (t.get("title") or "").strip()
        if "每日收盤行情(全部)" in title:
            return t

    for t in tables:
        title = (t.get("title") or "").strip()
        if "每日收盤行情" in title:
            return t

    print("[TWSE] 找不到『每日收盤行情』table（可能非交易日或 API 格式改版）")
    return None


def parse_twse_daily_close_rows(table: dict):
    fields = table.get("fields") or []
    data = table.get("data") or []

    def idx_of(*cands):
        for c in cands:
            if c in fields:
                return fields.index(c)
        return None

    i_symbol = idx_of("證券代號", "證券代碼")
    i_open   = idx_of("開盤價")
    i_high   = idx_of("最高價")
    i_low    = idx_of("最低價")
    i_close  = idx_of("收盤價")
    i_vol    = idx_of("成交股數", "成交數量")

    if None in (i_symbol, i_close):
        raise RuntimeError(f"[TWSE] 欄位不齊：fields={fields}")

    out = []
    for row in data:
        sym = str(row[i_symbol]).replace("\u3000", "").strip().upper()
        if not re.fullmatch(r"(\d{4,6}|\d{5}[A-Z])", sym):
            continue
        out.append({
            "symbol": sym,
            "open": to_num(row[i_open]) if i_open is not None else None,
            "high": to_num(row[i_high]) if i_high is not None else None,
            "low":  to_num(row[i_low]) if i_low is not None else None,
            "close": to_num(row[i_close]),
            "volume": to_int(row[i_vol]) if i_vol is not None else None,
        })
    return out


# ==============================
# TPEx fetch/parse
# ==============================
def yyyymmdd_to_roc_slash(yyyymmdd: str) -> str:
    """20260210 -> 115/02/10"""
    if not re.fullmatch(r"\d{8}", yyyymmdd):
        raise ValueError("DATE must be YYYYMMDD, e.g. 20260210")
    y = int(yyyymmdd[:4])
    m = int(yyyymmdd[4:6])
    d = int(yyyymmdd[6:8])
    roc_y = y - 1911
    return f"{roc_y:03d}/{m:02d}/{d:02d}"


def fetch_tpex_payload(date_yyyymmdd: str) -> dict:
    roc_d = yyyymmdd_to_roc_slash(date_yyyymmdd)
    params = {
        "l": "zh-tw",
        "d": roc_d,
        "se": "EW",   # ETF
        "o": "json",  # tables/fields/data
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.tpex.org.tw/",
        "Accept": "application/json, text/plain, */*",
    }
    r = requests.get(TPEX_URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return json.loads(r.text)


def pick_tpex_table(payload: dict):
    tables = payload.get("tables") or []
    if not tables:
        return None
    for t in tables:
        title = (t.get("title") or "").strip()
        if "每日收盤行情" in title:
            return t
    return tables[0]


def parse_tpex_daily_rows(table: dict):
    fields = table.get("fields") or []
    data = table.get("data") or []

    def idx_of(*cands):
        for c in cands:
            for i, f in enumerate(fields):
                if str(f).strip() == c:
                    return i
        return None

    i_symbol = idx_of("代號")
    i_open   = idx_of("開盤")
    i_high   = idx_of("最高")
    i_low    = idx_of("最低")
    i_close  = idx_of("收盤")
    i_vol    = idx_of("成交股數")

    if i_symbol is None or i_close is None:
        raise RuntimeError(f"[TPEx] 欄位不齊：fields={fields}")

    out = []
    for row in data:
        sym = str(row[i_symbol]).replace("\u3000", "").strip().upper()
        if not re.fullmatch(r"(\d{4,6}|\d{5}[A-Z])", sym):
            continue
        out.append({
            "symbol": sym,
            "open": to_num(row[i_open]) if i_open is not None else None,
            "high": to_num(row[i_high]) if i_high is not None else None,
            "low":  to_num(row[i_low]) if i_low is not None else None,
            "close": to_num(row[i_close]),
            "volume": to_int(row[i_vol]) if i_vol is not None else None,
        })
    return out


# ==============================
# Main sync
# ==============================
def sync_one_day(conn, exchange: str, date_yyyymmdd: str):
    trade_date = parse_date_yyyymmdd(date_yyyymmdd)

    # 0) master
    etf_map = fetch_etf_master_symbols_with_inception(conn, exchange)
    if not etf_map:
        print(f"[{exchange}] etf_master 沒有 active ETF，略過")
        return

    etf_symbols = set(etf_map.keys())
    print(f"[{exchange}] etf_master active ETFs = {len(etf_symbols)}")

    # 1) ensure partition
    with conn.cursor() as cur:
        ensure_month_partition(cur, trade_date)
    conn.commit()

    # 2) fetch + parse market rows
    if exchange == "TWSE":
        payload = fetch_twse_mi_index(date_yyyymmdd)
        table = pick_twse_daily_close_table(payload)
        if table is None:
            print(f"[TWSE] {date_yyyymmdd} Skip (可能非交易日)")
            return
        rows = parse_twse_daily_close_rows(table)
        print("[TWSE] table title =", table.get("title"))
        source = SOURCE_TWSE

    elif exchange == "TPEx":
        payload = fetch_tpex_payload(date_yyyymmdd)
        table = pick_tpex_table(payload)
        if table is None:
            print(f"[TPEx] {date_yyyymmdd} No tables (可能非交易日/休市/無資料)")
            return
        rows = parse_tpex_daily_rows(table)
        print("[TPEx] table title =", table.get("title"))
        source = SOURCE_TPEX

    else:
        raise RuntimeError(f"Unsupported EXCHANGE={exchange}")

    print(f"[{exchange}] market rows total =", len(rows))

    # 3) filter to ETFs in master
    row_syms = {r["symbol"] for r in rows}
    missing_in_market = sorted(list(etf_symbols - row_syms))
    print(f"[{exchange}] missing in market count =", len(missing_in_market))
    if missing_in_market:
        print(f"[{exchange}] missing in market sample (first 30) =", missing_in_market[:30])

    etf_rows = [r for r in rows if r["symbol"] in etf_symbols and r["close"] is not None]
    print(f"[{exchange}] rows after master filter =", len(etf_rows))

    if not etf_rows:
        print(f"[{exchange}] ⚠️ 這天沒有抓到任何 ETF 行情（可能休市/格式改變/無成交）")
        return

    # 4) upsert
    records = [
        (
            r["symbol"], exchange, trade_date,
            r["open"], r["high"], r["low"], r["close"], r["volume"],
            source
        )
        for r in etf_rows
    ]

    with conn.cursor() as cur:
        execute_values(cur, UPSERT_SQL, records, page_size=500)
    conn.commit()

    print(f"[{exchange}] ✅ 寫入 etf_prices：{trade_date} 共 {len(records)} 筆 ETF 行情")

    # 5) inception_date checker: should have but not written
    written_syms = fetch_written_symbols(conn, exchange, trade_date)

    should_have = set()
    for sym, inc in etf_map.items():
        if inc is None:
            continue
        if inc <= trade_date:
            should_have.add(sym)

    missing_after_write = sorted(list(should_have - written_syms))
    print(f"\n[{exchange}] === inception_date 之後應該有價格，但當天沒寫入 etf_prices 的 ETF ===")
    print(f"[{exchange}] should_have_count =", len(should_have))
    print(f"[{exchange}] missing_after_write_count =", len(missing_after_write))
    if missing_after_write:
        print(f"[{exchange}] missing_after_write sample (first 50) =", ", ".join(missing_after_write[:50]))
    print("")


def main():
    date_yyyymmdd = DATE_YYYYMMDD or dt.date.today().strftime("%Y%m%d")

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )
    try:
        for ex in EXCHANGES:
            sync_one_day(conn, ex, date_yyyymmdd)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
