#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
sync_etf_distributions_daily.py

- TWSE: 抓 https://www.twse.com.tw/zh/ETFortune/dividendList (HTML)
- TPEx: 抓 https://info.tpex.org.tw/api/etfExDiv (JSON)
- 寫入：public.etf_distributions
- PK: (symbol, exchange, ex_date)

用法：
  python sync_etf_distributions_daily.py
  EXCHANGES=TWSE python sync_etf_distributions_daily.py
  START_YEAR=2023 END_YEAR=2026 python sync_etf_distributions_daily.py
"""

import os
import re
import time
import decimal
from datetime import date
from typing import Optional, List, Tuple, Dict

import certifi
import requests
from bs4 import BeautifulSoup
import psycopg2
from psycopg2.extras import execute_values


# ==============================
# ENV
# ==============================
EXCHANGES = [x.strip() for x in os.getenv("EXCHANGES", "TWSE,TPEx").split(",") if x.strip()]

PG_HOST = os.getenv("PG_HOST", "127.0.0.1")
PG_PORT = int(os.getenv("PG_PORT", "5433"))
PG_DB   = os.getenv("PG_DB", "etfdb")
PG_USER = os.getenv("PG_USER", "etf")
PG_PASS = os.getenv("PG_PASS", "etfpass")

# ==============================
# 年份控制（最近 5 年）
# ==============================
CURRENT_YEAR = date.today().year
DEFAULT_START_YEAR = CURRENT_YEAR - 2
DEFAULT_END_YEAR   = CURRENT_YEAR

START_YEAR = int(os.getenv("START_YEAR", str(DEFAULT_START_YEAR)))
END_YEAR   = int(os.getenv("END_YEAR", str(DEFAULT_END_YEAR)))

SLEEP_SEC = float(os.getenv("SLEEP_SEC", "0.6"))

TWSE_URL = "https://www.twse.com.tw/zh/ETFortune/dividendList"
TPEX_API_URL = "https://info.tpex.org.tw/api/etfExDiv"

SOURCE_TWSE = "TWSE_ETFortune"
SOURCE_TPEX = "TPEx_API"


# ==============================
# DB
# ==============================
UPSERT_SQL = """
INSERT INTO public.etf_distributions (
  symbol, exchange, ex_date,
  record_date, pay_date,
  cash_amount, currency,
  frequency_tag, source
)
VALUES %s
ON CONFLICT (symbol, exchange, ex_date) DO UPDATE SET
  record_date = COALESCE(EXCLUDED.record_date, etf_distributions.record_date),
  pay_date    = COALESCE(EXCLUDED.pay_date, etf_distributions.pay_date),
  cash_amount = EXCLUDED.cash_amount,
  source      = EXCLUDED.source,
  retrieved_at = now()
;
"""

def get_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


# ==============================
# Common helpers
# ==============================
ROC_DATE_RE = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")

def parse_roc_date(s: str) -> Optional[date]:
    if not s:
        return None
    m = ROC_DATE_RE.search(str(s))
    if not m:
        return None
    roc_y, mm, dd = map(int, m.groups())
    try:
        return date(roc_y + 1911, mm, dd)
    except:
        return None

def parse_decimal_amount(s: str) -> Optional[decimal.Decimal]:
    if not s:
        return None
    s = str(s).strip().replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    return decimal.Decimal(s)


# ==============================
# TWSE
# ==============================
def fetch_twse_html(year: int) -> str:
    params = {"stkNo": "", "startDate": str(year), "endDate": str(year)}
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(TWSE_URL, params=params, headers=headers, verify=certifi.where())
    r.raise_for_status()
    return r.text

def extract_twse_records(html: str) -> List[Tuple]:
    soup = BeautifulSoup(html, "html.parser")
    table = None

    for t in soup.find_all("table"):
        th_text = " ".join(th.get_text(" ", strip=True) for th in t.find_all("th"))
        if ("除息交易日" in th_text) and ("收益分配金額" in th_text):
            table = t
            break

    if table is None:
        return []

    records = []
    for tr in table.select("tbody tr"):
        tds = tr.find_all("td")
        if len(tds) != 8:
            continue

        symbol = tds[0].get_text(strip=True)
        ex_date = parse_roc_date(tds[2].get_text())
        record_date = parse_roc_date(tds[3].get_text())
        pay_date = parse_roc_date(tds[4].get_text())
        cash_amount = parse_decimal_amount(tds[5].get_text())

        if symbol and ex_date and cash_amount is not None:
            records.append((
                symbol, "TWSE", ex_date,
                record_date, pay_date,
                cash_amount, None, None, SOURCE_TWSE
            ))

    return records


# ==============================
# TPEx
# ==============================
def fetch_tpex_json() -> List[dict]:
    r = requests.get(TPEX_API_URL, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    j = r.json()
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        for k in ("result", "data", "items", "records"):
            if k in j and isinstance(j[k], list):
                return j[k]
    return []

def extract_tpex_records(rows: List[dict]) -> List[Tuple]:
    out = []
    CURRENT_ROC_YEAR = date.today().year - 1911
    MIN_ROC_YEAR = CURRENT_ROC_YEAR - 2
    MAX_ROC_YEAR = CURRENT_ROC_YEAR

    for it in rows:
        y = it.get("year")
        if not y:
            continue

        y = int(y)
        if y < MIN_ROC_YEAR or y > MAX_ROC_YEAR:
            continue


        symbol = (it.get("stockNo") or "").strip()
        ex_date = parse_roc_date(it.get("divDate"))
        pay_date = parse_roc_date(it.get("inDate"))
        record_date = parse_roc_date(it.get("inBaseDate"))
        cash_amount = parse_decimal_amount(it.get("amount"))

        if symbol and ex_date:
            out.append((
                symbol, "TPEx", ex_date,
                record_date, pay_date,
                cash_amount or decimal.Decimal("0"),
                None, None, SOURCE_TPEX
            ))

    return out

def fetch_allowed_symbols(conn, exchange: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol
            FROM public.etf_master
            WHERE exchange=%s
            """,
            (exchange,)
        )
        return {str(r[0]).strip().upper() for r in cur.fetchall() if r[0]}



# ==============================
# MAIN
# ==============================
def main():
    conn = get_conn()

    try:
        for ex in EXCHANGES:
            ex = ex.strip()

            print("\n==========================")
            print(f"Running exchange: {ex}")
            print("==========================")

            # 0) 先抓 etf_master 允許的 symbols（避免 FK）
            allowed_symbols = fetch_allowed_symbols(conn, ex)
            print(f"[{ex}] etf_master symbols: {len(allowed_symbols)}")
            if not allowed_symbols:
                print(f"[{ex}] ⚠️ allowed_symbols is empty. Check etf_master.exchange value.")
                continue

            # 1) 依交易所抓資料
            all_records = []

            if ex == "TWSE":
                for y in range(START_YEAR, END_YEAR + 1):
                    print(f"[TWSE] Year {y}")
                    html = fetch_twse_html(y)
                    rec = extract_twse_records(html)
                    print(f"[TWSE] Parsed {len(rec)} rows")
                    all_records.extend(rec)
                    time.sleep(SLEEP_SEC)

            elif ex == "TPEx":
                rows = fetch_tpex_json()
                rec = extract_tpex_records(rows)
                print(f"[TPEx] Parsed {len(rec)} rows")
                all_records.extend(rec)

            else:
                print(f"Skip unsupported exchange={ex}")
                continue

            if not all_records:
                print(f"[{ex}] No records fetched.")
                continue

            # 2) dedup by (symbol, exchange, ex_date)
            dedup = {}
            for r in all_records:
                key = (r[0], r[1], r[2])
                dedup[key] = r

            dedup_values = list(dedup.values())
            print(f"[{ex}] After dedup: {len(dedup_values)} rows")

            # 3) 印出「有資料但不在 etf_master」的 symbols（你要的）
            data_symbols = {r[0].strip().upper() for r in dedup_values if r[0]}
            missing_in_master = sorted(list(data_symbols - allowed_symbols))

            print(f"[{ex}] symbols in data: {len(data_symbols)}")
            print(f"[{ex}] ❌ symbols missing in etf_master: {len(missing_in_master)}")
            if missing_in_master:
                print(f"[{ex}] missing sample (first 50): {', '.join(missing_in_master[:50])}")

            # 4) 只保留 etf_master 內的，避免 FK 爆掉
            values = [r for r in dedup_values if str(r[0]).strip().upper() in allowed_symbols]
            print(f"[{ex}] ✅ rows to upsert (after master filter): {len(values)}")

            if not values:
                print("No rows to upsert.")
                continue

            # 5) upsert
            with conn:
                with conn.cursor() as cur:
                    execute_values(cur, UPSERT_SQL, values, page_size=1000)

            print(f"[{ex}] ✅ Upserted {len(values)} rows into etf_distributions")

    finally:
        conn.close()



if __name__ == "__main__":
    main()