#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import re
import datetime as dt
from decimal import Decimal, InvalidOperation

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


TABLE_NAME = os.getenv("FEES_TABLE", "etf_fees")
SOURCE_TAG = os.getenv("SOURCE_TAG", "wantgoo_manual_json")

AS_OF_DATE_ENV = os.getenv("AS_OF_DATE")  # YYYYMMDD optional
JSON_PATH = os.getenv(
    "JSON_PATH",
    str(BASE_DIR / "data" / "wantgoo_basic_data.json")
)

# =========================
# Helpers
# =========================

def get_exchanges():
    return ["TWSE", "TPEx"]

def parse_as_of_date() -> dt.date:
    if AS_OF_DATE_ENV and re.fullmatch(r"\d{8}", AS_OF_DATE_ENV):
        return dt.date(
            int(AS_OF_DATE_ENV[:4]),
            int(AS_OF_DATE_ENV[4:6]),
            int(AS_OF_DATE_ENV[6:8])
        )
    return dt.date.today()


def parse_decimal(x):
    if x is None:
        return None
    try:
        d = Decimal(str(x))
    except InvalidOperation:
        return None

    # Wantgoo 常用 -9999 代表缺值；負數費率不合理
    if d < Decimal("0"):
        return None

    return d


def read_json_file(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        j = json.load(f)
    if not isinstance(j, list):
        raise ValueError("JSON 最外層必須是 list，例如：[ {...}, {...} ]")
    return j


def fetch_master_symbols(conn, exchange: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol
            FROM public.etf_master
            WHERE exchange=%s AND status='active'
            """,
            (exchange,)
        )
        return {
            str(r[0]).replace("\u3000", "").strip().upper()
            for r in cur.fetchall()
            if r[0]
        }


# =========================
# UPSERT SQL
# =========================

UPSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    symbol, exchange,
    mgmt_fee, custody_fee, ter,
    fee_as_of_date, source
)
VALUES %s
ON CONFLICT (symbol, exchange)
DO UPDATE SET
    mgmt_fee       = EXCLUDED.mgmt_fee,
    custody_fee    = EXCLUDED.custody_fee,
    ter            = EXCLUDED.ter,
    fee_as_of_date = EXCLUDED.fee_as_of_date,
    source         = EXCLUDED.source,
    retrieved_at   = NOW()
;
"""


# =========================
# Main
# =========================

def main():
    as_of_date = parse_as_of_date()
    exchanges = get_exchanges()

    all_rows = read_json_file(JSON_PATH)
    print("json rows total =", len(all_rows))

    # map: stockNo -> (mgmt_fee, custody_fee)
    basic_map = {}
    for it in all_rows:
        sym = (it.get("stockNo") or "").strip().upper()
        if not sym:
            continue

        basic_map[sym] = (
            parse_decimal(it.get("managementFee")),
            parse_decimal(it.get("custodyFee")),
        )

    print("distinct stockNo =", len(basic_map))

    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS
    )

    try:
        values = []

        for ex in exchanges:
            print("\n==============================")
            print(f"Processing exchange: {ex}")
            print("==============================")

            master_syms = fetch_master_symbols(conn, ex)
            print(f"[{ex}] etf_master active =", len(master_syms))

            updated_syms = set()
            missing_in_json = []
            no_fee_data = []

            for sym in master_syms:

                if sym not in basic_map:
                    missing_in_json.append(sym)
                    continue

                mgmt_fee, custody_fee = basic_map[sym]

                if mgmt_fee is None and custody_fee is None:
                    no_fee_data.append(sym)
                    continue

                ter = None
                if mgmt_fee is not None and custody_fee is not None:
                    ter = mgmt_fee + custody_fee

                values.append((
                    sym,
                    ex,
                    mgmt_fee,
                    custody_fee,
                    ter,
                    as_of_date,
                    SOURCE_TAG
                ))

                updated_syms.add(sym)

            # ===== 印出未更新清單 =====
            print(f"[{ex}] will update =", len(updated_syms))

            print(f"[{ex}] missing_in_json =", len(missing_in_json))
            if missing_in_json:
                print("  sample:", missing_in_json[:20])

            print(f"[{ex}] no_fee_data =", len(no_fee_data))
            if no_fee_data:
                print("  sample:", no_fee_data[:20])

            not_updated = set(master_syms) - updated_syms
            print(f"[{ex}] total_not_updated =", len(not_updated))
            if not_updated:
                print("  sample:", list(not_updated)[:20])

        if not values:
            print("No rows to upsert.")
            return

        with conn:
            with conn.cursor() as cur:
                execute_values(cur, UPSERT_SQL, values, page_size=1000)

        print("\n=== DONE ===")
        print("fee_as_of_date =", as_of_date.isoformat())
        print("upsert rows =", len(values))

    finally:
        conn.close()


if __name__ == "__main__":
    main()
