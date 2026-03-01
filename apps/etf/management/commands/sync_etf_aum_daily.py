#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
sync_aum_daily.py
- TWSE: Selenium 抓 https://www.twse.com.tw/zh/ETFortune/products
- TPEx: requests 抓 https://info.tpex.org.tw/api/etfFilter
- 寫入：public.etf_aum_daily (symbol, exchange, as_of_date) PK

用法：
  python sync_aum_daily.py
  EXCHANGES=TWSE,TPEx python sync_aum_daily.py
  EXCHANGES=TWSE python sync_aum_daily.py
  DATE=20260223 python sync_aum_daily.py   # 交易日/資料日（YYYYMMDD）
"""

import os
import re
import time
import json
import datetime as dt
from typing import Optional, List, Tuple, Dict

import requests
import psycopg2
from psycopg2.extras import execute_values

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ==============================
# ENV
# ==============================
# Exchanges to run
EXCHANGES = [x.strip() for x in os.getenv("EXCHANGES", "TWSE,TPEx").split(",") if x.strip()]

# as_of_date (資料日)
DATE_YYYYMMDD = os.getenv("DATE")  # e.g. 20260223, if None => today

# DB
PG_HOST = os.getenv("PG_HOST", "127.0.0.1")
PG_PORT = int(os.getenv("PG_PORT", "5433"))
PG_DB   = os.getenv("PG_DB", "etfdb")
PG_USER = os.getenv("PG_USER", "etf")
PG_PASS = os.getenv("PG_PASS", "etfpass")

# Sources
SOURCE_TWSE = os.getenv("SOURCE_TWSE", "TWSE_ETFortune")
SOURCE_TPEX = os.getenv("SOURCE_TPEX", "tpex_etfFilter")

# TWSE Selenium
TWSE_URL = os.getenv("TWSE_ETFORTUNE_URL", "https://www.twse.com.tw/zh/ETFortune/products")
HEADLESS = os.getenv("HEADLESS", "1") == "1"
WINDOW_SIZE = os.getenv("WINDOW_SIZE", "1600,1200")
WAIT_SEC = int(os.getenv("WAIT_SEC", "40"))
MIN_ROWS = int(os.getenv("MIN_ROWS", "150"))
MAX_SCROLLS = int(os.getenv("MAX_SCROLLS", "80"))
SLEEP_SEC = float(os.getenv("SLEEP_SEC", "0.6"))

# TPEx API
TPEX_ETF_FILTER_URL = os.getenv("TPEX_ETF_FILTER_URL", "https://info.tpex.org.tw/api/etfFilter")
# 若要帶 payload（篩選/排序/分頁）可自行填；通常空 dict 也能回
TPEX_ETF_FILTER_PAYLOAD = json.loads(os.getenv("TPEX_ETF_FILTER_PAYLOAD", "{}"))


# ==============================
# DB helpers
# ==============================
def get_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )

def parse_date_yyyymmdd(s: str) -> dt.date:
    if not re.fullmatch(r"\d{8}", s):
        raise ValueError("DATE must be YYYYMMDD, e.g. 20260223")
    return dt.date(int(s[0:4]), int(s[4:6]), int(s[6:8]))

def fetch_active_symbols(conn, exchange: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT symbol
            FROM public.etf_master
            WHERE exchange=%s AND status='active'
        """, (exchange,))
        return {str(r[0]).replace("\u3000", "").strip().upper() for r in cur.fetchall() if r[0]}

AUM_UPSERT_SQL = """
INSERT INTO public.etf_aum_daily (symbol, exchange, as_of_date, aum, source)
VALUES %s
ON CONFLICT (symbol, exchange, as_of_date) DO UPDATE
SET aum = EXCLUDED.aum,
    source = EXCLUDED.source,
    retrieved_at = now()
;
"""


# ==============================
# Common parse helpers
# ==============================
def to_number(x: str) -> Optional[float]:
    """把 '1,234.56' / '-' / '11,774 (百萬)' 轉成 float 或 None"""
    if x is None:
        return None
    s = str(x).strip()
    if not s or s in ("-", "—", "–"):
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return None
    num = m.group(0).replace(",", "")
    try:
        return float(num)
    except Exception:
        return None

def to_numeric_strict(s: str) -> Optional[float]:
    """TPEx JSON 可能是 '1,234.56' or '1234'"""
    if s is None:
        return None
    s = str(s).strip()
    if s == "":
        return None
    s = s.replace(",", "")
    if not re.match(r"^-?\d+(\.\d+)?$", s):
        return None
    return float(s)


# ==============================
# TWSE (Selenium) scrape
# ==============================
def build_driver() -> webdriver.Chrome:
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--window-size={WINDOW_SIZE}")

    # anti-bot (best-effort)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--lang=zh-TW")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(service=Service(), options=options)

    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })
    except Exception:
        pass

    return driver

def wait_for_rows(driver: webdriver.Chrome, wait: WebDriverWait) -> int:
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.stock-table")))
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.stock-table tbody tr.etf")))

    def count_rows() -> int:
        return len(driver.find_elements(By.CSS_SELECTOR, "table.stock-table tbody tr.etf"))

    try:
        wait.until(lambda d: count_rows() >= MIN_ROWS)
        return count_rows()
    except Exception:
        try:
            wait.until(lambda d: count_rows() > 8)
        except Exception:
            pass
        return count_rows()

def scroll_to_load_more(driver: webdriver.Chrome) -> int:
    def row_count() -> int:
        return len(driver.find_elements(By.CSS_SELECTOR, "table.stock-table tbody tr.etf"))

    last = row_count()
    for _ in range(MAX_SCROLLS):
        driver.execute_script("""
        const table = document.querySelector('table.stock-table');
        let el = table;
        while (el && el !== document.body) {
          const s = getComputedStyle(el);
          if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) break;
          el = el.parentElement;
        }
        if (!el || el === document.body) el = document.scrollingElement;
        el.scrollTop = el.scrollHeight;
        window.scrollTo(0, document.body.scrollHeight);
        """)
        time.sleep(SLEEP_SEC)

        cur = row_count()
        if cur == last:
            time.sleep(SLEEP_SEC)
            cur2 = row_count()
            if cur2 == last:
                break
            cur = cur2
        last = cur
    return last

def extract_table(driver: webdriver.Chrome) -> Tuple[List[str], List[List[str]]]:
    ths = driver.find_elements(By.CSS_SELECTOR, "table.stock-table thead th")
    headers = [th.text.strip() for th in ths]
    rows = driver.find_elements(By.CSS_SELECTOR, "table.stock-table tbody tr.etf")
    data: List[List[str]] = []
    for tr in rows:
        tds = tr.find_elements(By.CSS_SELECTOR, "td")
        data.append([td.text.strip() for td in tds])
    return headers, data

def find_col_index(headers: List[str], keywords: List[str]) -> Optional[int]:
    norm = [h.replace(" ", "") for h in headers]
    for i, h in enumerate(norm):
        if any(k in h for k in keywords):
            return i
    return None

def scrape_twse_aum_records() -> List[Tuple[str, float]]:
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SEC)
    try:
        driver.get(TWSE_URL)

        rows0 = wait_for_rows(driver, wait)
        print(f"[TWSE] ✅ initial rows(etf)={rows0}")

        rows1 = scroll_to_load_more(driver)
        print(f"[TWSE] ✅ after scroll rows(etf)={rows1}")

        headers, data = extract_table(driver)
    finally:
        driver.quit()

    if not data:
        raise RuntimeError("[TWSE] data empty")

    col_n = len(data[0])
    if len(headers) != col_n:
        headers = [f"col_{i}" for i in range(col_n)]

    idx_symbol = find_col_index(headers, ["證券代號", "代號", "商品代號"])
    idx_aum = find_col_index(headers, ["資產規模", "基金規模", "規模", "AUM"])

    if idx_symbol is None:
        idx_symbol = 0
    if idx_aum is None:
        idx_aum = 4  # fallback

    records: List[Tuple[str, float]] = []
    for row in data:
        if len(row) <= max(idx_symbol, idx_aum):
            continue
        symbol = row[idx_symbol].strip().upper()
        aum = to_number(row[idx_aum])
        if aum is None:
            continue
        records.append((symbol, aum))

    print(f"[TWSE] ✅ parsed records={len(records)} (symbol,aum)")
    return records


# ==============================
# TPEx (requests) fetch
# ==============================
def fetch_tpex_json_rows() -> list[dict]:
    resp = requests.post(TPEX_ETF_FILTER_URL, json=TPEX_ETF_FILTER_PAYLOAD, timeout=30)
    resp.raise_for_status()
    j = resp.json()
    if j.get("status") != "success" or "data" not in j:
        raise RuntimeError(f"[TPEx] Unexpected response: keys={list(j.keys())}, status={j.get('status')}")
    return j["data"]

def extract_tpex_aum_records(rows: list[dict]) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    for r in rows:
        symbol = (r.get("stockNo") or "").strip().upper()
        if not symbol:
            continue
        aum = to_numeric_strict(r.get("totalAv"))
        if aum is None:
            continue
        out.append((symbol, aum))
    print(f"[TPEx] ✅ parsed records={len(out)} (symbol,aum)")
    return out


# ==============================
# Write to DB (common)
# ==============================
def upsert_aum(conn, exchange: str, as_of_date: dt.date, records: List[Tuple[str, float]], source: str):
    if not records:
        print(f"[{exchange}] No records to write.")
        return

    active = fetch_active_symbols(conn, exchange)
    print(f"[{exchange}] active symbols in DB: {len(active)}")

    # keep only active
    filtered = [(s, a) for (s, a) in records if s in active]
    print(f"[{exchange}] after active filter: {len(filtered)}")

    scraped_set = {s for (s, _) in records}
    filtered_set = {s for (s, _) in filtered}
    active_set = set(active)

    dropped_not_active = sorted(list(scraped_set - active_set))
    if dropped_not_active:
        print(f"[{exchange}] ❌ dropped_not_active: {len(dropped_not_active)} sample={dropped_not_active[:30]}")

    active_not_scraped = sorted(list(active_set - scraped_set))
    if active_not_scraped:
        print(f"[{exchange}] ℹ️ active_not_scraped: {len(active_not_scraped)} sample={active_not_scraped[:30]}")

    if not filtered:
        print(f"[{exchange}] ⚠️ filtered empty (請檢查 symbol 格式/etf_master exchange/status)")
        return

    values = [(sym, exchange, as_of_date, float(aum), source) for sym, aum in filtered]

    with conn.cursor() as cur:
        execute_values(cur, AUM_UPSERT_SQL, values, page_size=1000)
    conn.commit()

    print(f"[{exchange}] ✅ AUM 匯入完成：{len(values)} 筆（as_of_date={as_of_date}） → etf_aum_daily")


# ==============================
# Main
# ==============================
def main():
    as_of_date = parse_date_yyyymmdd(DATE_YYYYMMDD) if DATE_YYYYMMDD else dt.date.today()

    conn = get_conn()
    try:
        for ex in EXCHANGES:
            ex = ex.strip()
            print("\n==============================")
            print(f"Run exchange: {ex} (as_of_date={as_of_date})")
            print("==============================")

            if ex == "TWSE":
                records = scrape_twse_aum_records()
                upsert_aum(conn, "TWSE", as_of_date, records, SOURCE_TWSE)

            elif ex == "TPEx":
                rows = fetch_tpex_json_rows()
                records = extract_tpex_aum_records(rows)
                upsert_aum(conn, "TPEx", as_of_date, records, SOURCE_TPEX)

            else:
                print(f"Skip unsupported exchange={ex}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
