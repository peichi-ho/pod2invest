"""
幫大表 IndustryClassification 補上標的名稱(zh_name / en_name / name_source)。

名稱來源優先順序：
  台股上市(.TW)：TWSE 公司基本資料 t187ap03_L(公司簡稱/英文簡稱)
                → 查不到(ETF等)用 TWSE 每日收盤行情 STOCK_DAY_ALL 的 Name
  台股上櫃(.TWO)：TPEx 公司基本資料 mopsfin_t187ap03_O(CompanyAbbreviation/Symbol)
                → 查不到用 TPEx 每日收盤行情的 CompanyName
  中文名還是空的：用 TickerMap 既有的 zh_name 補(美股/指數/期貨主要靠這個)
  非台股的英文名：yfinance shortName，沒有才用 longName
  台股官方跟 TickerMap 都查不到中文名，最後才試 yfinance 英文名

美股中文名只從 TickerMap 補，不自己翻譯——沒有權威來源的中文名寧可留空，
display_name 會退回英文名。兩個名稱都查不到的會列成清單。

預設是 dry run(只讀資料庫、只印統計)，加 --commit 才寫入 summariesdb。

用法：
  python3 manage.py backfill_industry_names
  python3 manage.py backfill_industry_names --yf-cache /tmp/yf_names.json --report /tmp/names.csv
  python3 manage.py backfill_industry_names --commit --yf-cache /tmp/yf_names.json
"""
import csv
import json
import os
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests
from django.core.management.base import BaseCommand

from apps.summaries.models import IndustryClassification, TickerMap

DB = "summariesdb"

_TWSE_COMPANY_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
_TPEX_COMPANY_INFO_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
_TWSE_QUOTES_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
_TPEX_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"


def _clean(s) -> str:
    # 官方資料常帶全形空白(例如 "MORNSUN　")，str.strip() 會一併去掉
    return (s or "").strip()


def _get_json(url: str) -> list[dict]:
    res = requests.get(url, timeout=30)
    res.raise_for_status()
    return res.json()


def _fetch_official_names() -> dict[str, dict[str, tuple[str, str]]]:
    """回傳 {來源: {股票代號: (中文名, 英文名)}}，來源是 twse/tpex/twse_quote/tpex_quote。"""
    out: dict[str, dict[str, tuple[str, str]]] = {}
    out["twse"] = {
        _clean(r.get("公司代號")): (_clean(r.get("公司簡稱")), _clean(r.get("英文簡稱")))
        for r in _get_json(_TWSE_COMPANY_INFO_URL) if _clean(r.get("公司代號"))
    }
    out["tpex"] = {
        _clean(r.get("SecuritiesCompanyCode")): (_clean(r.get("CompanyAbbreviation")), _clean(r.get("Symbol")))
        for r in _get_json(_TPEX_COMPANY_INFO_URL) if _clean(r.get("SecuritiesCompanyCode"))
    }
    out["twse_quote"] = {
        _clean(r.get("Code")): (_clean(r.get("Name")), "")
        for r in _get_json(_TWSE_QUOTES_URL) if _clean(r.get("Code"))
    }
    out["tpex_quote"] = {
        _clean(r.get("SecuritiesCompanyCode")): (_clean(r.get("CompanyName")), "")
        for r in _get_json(_TPEX_QUOTES_URL) if _clean(r.get("SecuritiesCompanyCode"))
    }
    return out


def _tickermap_zh_names() -> dict[str, str]:
    """{ticker: zh_name}。同一個ticker有多筆別名時，人工確認過的優先，再取出現最多次的。"""
    by_ticker: dict[str, Counter] = defaultdict(Counter)
    for ticker, zh_name, verified in TickerMap.objects.using(DB).values_list("ticker", "zh_name", "verified"):
        zh_name = _clean(zh_name)
        if zh_name:
            by_ticker[ticker][zh_name] += 10 if verified else 1
    return {t: c.most_common(1)[0][0] for t, c in by_ticker.items()}


def _yf_name(ticker: str) -> str:
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return ""
    return _clean(info.get("shortName")) or _clean(info.get("longName"))


class Command(BaseCommand):
    help = "幫 IndustryClassification 補上 zh_name / en_name(預設 dry run，--commit 才寫入)"

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="實際寫入 summariesdb")
        parser.add_argument("--yf-cache", help="yfinance 查詢結果快取 JSON 檔(讀取+寫回)，避免重跑時重查")
        parser.add_argument("--report", help="把每一筆的結果輸出成 CSV，方便人工檢查")
        parser.add_argument("--workers", type=int, default=4, help="yfinance 並行數(預設4)")

    def handle(self, *args, commit=False, yf_cache=None, report=None, workers=4, **opts):
        # 只取既有欄位：dry run 要能在 migration 還沒套用前執行
        rows = list(IndustryClassification.objects.using(DB).values("id", "ticker", "market", "source"))
        self.stdout.write(f"IndustryClassification 共 {len(rows)} 筆")

        official = _fetch_official_names()
        self.stdout.write("官方資料：" + "、".join(f"{k} {len(v)} 筆" for k, v in official.items()))
        tm_zh = _tickermap_zh_names()
        self.stdout.write(f"TickerMap 有中文名的 ticker：{len(tm_zh)} 個")

        # 第一輪：官方資料 + TickerMap
        results = {}
        for r in rows:
            ticker = r["ticker"]
            upper = ticker.upper()
            symbol = ticker.split(".")[0]
            zh, en, zh_src, en_src = "", "", "", ""
            if upper.endswith(".TW"):
                sources = ("twse", "twse_quote")
            elif upper.endswith(".TWO"):
                sources = ("tpex", "tpex_quote")
            else:
                sources = ()
            for src in sources:
                name_zh, name_en = official[src].get(symbol, ("", ""))
                if name_zh and not zh:
                    zh, zh_src = name_zh, src
                if name_en and not en:
                    en, en_src = name_en, src
            if not zh and tm_zh.get(ticker):
                zh, zh_src = tm_zh[ticker], "tickermap"
            results[ticker] = {"zh": zh, "en": en, "zh_src": zh_src, "en_src": en_src}

        # 第二輪：yfinance 英文名——非台股全部查；台股只查官方跟TickerMap都沒有中文名的
        need_yf = [
            t for t, res in results.items()
            if not res["en"] and (not t.upper().endswith((".TW", ".TWO")) or not res["zh"])
        ]
        cache = {}
        if yf_cache and os.path.exists(yf_cache):
            with open(yf_cache, encoding="utf-8") as f:
                cache = json.load(f)
        todo = [t for t in need_yf if t not in cache]
        self.stdout.write(f"需要 yfinance 英文名：{len(need_yf)} 筆(快取已有 {len(need_yf) - len(todo)}，要查 {len(todo)})")
        if todo:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for i, (t, name) in enumerate(zip(todo, pool.map(_yf_name, todo)), 1):
                    cache[t] = name
                    if i % 50 == 0 or i == len(todo):
                        self.stdout.write(f"  yfinance [{i}/{len(todo)}]")
            if yf_cache:
                with open(yf_cache, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False, indent=1)
        for t in need_yf:
            if cache.get(t):
                results[t]["en"], results[t]["en_src"] = cache[t], "yfinance"

        # 統計
        market_of = {r["ticker"]: r["market"] for r in rows}
        def name_source(res):
            srcs = [s for s in (res["zh_src"], res["en_src"]) if s]
            return "+".join(dict.fromkeys(srcs))  # 中英同來源只寫一次

        has_zh = sum(1 for res in results.values() if res["zh"])
        has_en = sum(1 for res in results.values() if res["en"])
        has_any = sum(1 for res in results.values() if res["zh"] or res["en"])
        missing = sorted(t for t, res in results.items() if not (res["zh"] or res["en"]))
        en_only_by_market = Counter(market_of[t] or "(空)" for t, res in results.items() if res["en"] and not res["zh"])

        self.stdout.write("")
        self.stdout.write(f"有名稱(中或英)：{has_any} / {len(rows)}")
        self.stdout.write(f"  有中文名：{has_zh}　有英文名：{has_en}")
        self.stdout.write(f"  只有英文名(display_name 會顯示英文)：{dict(en_only_by_market)}")
        self.stdout.write("名稱來源分佈：")
        for src, n in Counter(name_source(res) for res in results.values()).most_common():
            self.stdout.write(f"  {src or '(查無)'}: {n}")
        self.stdout.write(f"查不到名稱：{len(missing)} 筆")
        for t in missing:
            self.stdout.write(f"  {t}  market={market_of[t] or '(空)'}")

        # 台股但目前官方資料(基本資料跟行情)都查無：多半是已轉上市/下櫃的舊列，名稱可能不可信
        not_official = sorted(
            t for t, res in results.items()
            if t.upper().endswith((".TW", ".TWO"))
            and res["zh_src"] not in ("twse", "tpex", "twse_quote", "tpex_quote")
        )
        self.stdout.write(f"台股但官方資料已查無(可能已轉上市/下櫃，名稱要人工確認)：{len(not_official)} 筆")
        for t in not_official:
            res = results[t]
            self.stdout.write(f"  {t}  zh={res['zh'] or '-'}  en={res['en'] or '-'}  來源={name_source(res) or '查無'}")

        if report:
            with open(report, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["ticker", "market", "zh_name", "en_name", "name_source"])
                for r in rows:
                    res = results[r["ticker"]]
                    w.writerow([r["ticker"], r["market"], res["zh"], res["en"], name_source(res)])
            self.stdout.write(f"明細已輸出：{report}")

        if not commit:
            self.stdout.write(self.style.WARNING("dry run，沒有寫入資料庫。確認後加 --commit。"))
            return

        # 寫入：查不到的欄位保留原值，不會把已有的名稱清空
        objs = list(IndustryClassification.objects.using(DB).all())
        changed = []
        for obj in objs:
            res = results.get(obj.ticker)
            if not res:
                continue
            new_zh = res["zh"] or obj.zh_name
            new_en = res["en"] or obj.en_name
            new_src = name_source(res) or obj.name_source
            if (new_zh, new_en, new_src) != (obj.zh_name, obj.en_name, obj.name_source):
                obj.zh_name, obj.en_name, obj.name_source = new_zh, new_en, new_src
                changed.append(obj)
        IndustryClassification.objects.using(DB).bulk_update(
            changed, ["zh_name", "en_name", "name_source"], batch_size=500
        )
        self.stdout.write(self.style.SUCCESS(f"已寫入 {len(changed)} 筆"))
