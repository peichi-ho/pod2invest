"""
把回測紀錄(BacktestingRecord)、情緒分數(StockSentimentScore)、小表(TickerMap)用到、
但大表 IndustryClassification 還沒收錄的 ticker 補進大表（分類＋名稱）。

節目一直會講到新標的，大表沒收錄時前端只能退回顯示節目原話，所以建議每次
batch_summarize 跑完後跑一次。

判斷方式：
  台股(.TW/.TWO)：用 TWSE／TPEx 官方資料（公司基本資料＋每日行情）找代號。
    - 官方資料裡是上市、ticker 卻寫 .TWO（或反過來）→ 補正確的那一個，並列出來。
      resolve_ticker() 會先查大表決定 .TW/.TWO，補上後之後解析就會對，
      已存的紀錄再用 resync_backtesting_tickers 對齊。
    - 公司 → 官方產業分類；只在行情裡出現的（ETF）→ 產業「ETF」。
    - 官方資料都查不到 → 不補，列在「查不到」。
  其他：查 yfinance，依 quoteType 分成 ETF／指數／期貨／外匯／加密貨幣／個股；
    個股的產業用 yfinance 的 industry 翻成中文。Yahoo 也查不到股價的不補，列出來。

中文名只用官方資料或 TickerMap 既有的 zh_name，不自己翻譯；英文名用 yfinance。
預設 dry run，加 --commit 才寫入 summariesdb。

用法：
  python3 manage.py sync_industry_classification
  python3 manage.py sync_industry_classification --commit
"""
import time

import requests
from django.core.management.base import BaseCommand

from apps.summaries.models import BacktestingRecord, IndustryClassification, StockSentimentScore, TickerMap
from apps.summaries.services.industry_classification import (
    SECTOR_ZH, TWSE_INDUSTRY_NAMES, translate_yfinance_industry,
)

DB = "summariesdb"

_SOURCES = {
    "twse": ("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "公司代號", "公司簡稱", "英文簡稱", "產業別"),
    "tpex": ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", "SecuritiesCompanyCode",
             "CompanyAbbreviation", "Symbol", "SecuritiesIndustryCode"),
    "twse_quote": ("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", "Code", "Name", None, None),
    "tpex_quote": ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                   "SecuritiesCompanyCode", "CompanyName", None, None),
}
_QUOTE_TYPE_INDUSTRY = {"ETF": "ETF", "MUTUALFUND": "ETF", "INDEX": "指數", "FUTURE": "期貨",
                        "CURRENCY": "外匯", "CRYPTOCURRENCY": "加密貨幣"}


def _clean(s) -> str:
    return (s or "").strip()


def _get_json(url: str) -> list[dict]:
    # 櫃買中心的 API 偶爾會連線中斷，重試幾次
    for attempt in range(3):
        try:
            res = requests.get(url, timeout=30)
            res.raise_for_status()
            return res.json()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)


def _fetch_official() -> dict[str, dict[str, tuple[str, str, str]]]:
    """{來源: {代號: (中文名, 英文名, 產業代碼)}}"""
    out = {}
    for src, (url, k_code, k_zh, k_en, k_ind) in _SOURCES.items():
        out[src] = {
            _clean(r.get(k_code)): (_clean(r.get(k_zh)), _clean(r.get(k_en)) if k_en else "",
                                    _clean(r.get(k_ind)) if k_ind else "")
            for r in _get_json(url) if _clean(r.get(k_code))
        }
    return out


def _referenced_tickers() -> tuple[dict[str, int], dict[str, set[str]], set[str]]:
    """回傳 ({ticker: 被引用的筆數}, {ticker: 節目原話集合}, 小表人工確認過的 ticker)，三張表合計。"""
    counts: dict[str, int] = {}
    assets: dict[str, set[str]] = {}
    for qs in (
        BacktestingRecord.objects.using(DB).exclude(ticker="").values_list("ticker", "asset"),
        StockSentimentScore.objects.using(DB).exclude(ticker="").values_list("ticker", "asset_name"),
        TickerMap.objects.using(DB).exclude(ticker="").values_list("ticker", "asset_name"),
    ):
        for t, a in qs.iterator():
            counts[t] = counts.get(t, 0) + 1
            assets.setdefault(t, set()).add((a or "").strip())
    verified = set(TickerMap.objects.using(DB).filter(verified=True).values_list("ticker", flat=True))
    return counts, assets, verified


def _is_raw_passthrough(ticker: str, assets: set[str]) -> bool:
    """節目原話都跟 ticker 一模一樣(例如原話「WTI」→ ticker「WTI」)：這是 resolve_ticker 把英文縮寫
    原樣當成 ticker，不保證是節目講的那家公司（WTI 其實是原油、BYD 其實是比亞迪），要人工確認。"""
    if "." in ticker or ticker.startswith("^") or "=" in ticker or "-" in ticker:
        return False
    return all(a.upper() == ticker.upper() for a in assets)


def _sample_context(ticker: str) -> str:
    r = (BacktestingRecord.objects.using(DB).filter(ticker=ticker).exclude(thesis="")
         .values_list("thesis", flat=True).first())
    if not r:
        r = (StockSentimentScore.objects.using(DB).filter(ticker=ticker).exclude(rationale="")
             .values_list("rationale", flat=True).first())
    return (r or "").replace("\n", " ")[:60]


def _classify_tw(ticker: str, official: dict) -> tuple[dict | None, str]:
    code = ticker.split(".")[0].upper()
    listed = {"twse": ".TW", "tpex": ".TWO", "twse_quote": ".TW", "tpex_quote": ".TWO"}
    for src in ("twse", "tpex", "twse_quote", "tpex_quote"):
        if code not in official[src]:
            continue
        zh, en, ind_code = official[src][code]
        real = code + listed[src]
        if src in ("twse", "tpex"):
            row = dict(industry_code=ind_code, industry_name=TWSE_INDUSTRY_NAMES.get(ind_code, ind_code),
                       source=src)
        else:
            row = dict(industry_code="", industry_name="ETF", source="manual")
        row.update(ticker=real, market="TW", sector_name="", zh_name=zh, en_name=en, name_source=src)
        note = f"代號後綴寫錯，官方資料是 {real}" if real != ticker else ""
        return row, note
    return None, "TWSE／TPEx 官方資料都查不到（可能已下市，或代號錯）"


def _classify_other(ticker: str, tm_zh: dict) -> tuple[dict | None, str]:
    import yfinance as yf
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        has_price = not tk.history(period="1mo").empty
    except Exception:
        info, has_price = {}, False
    if not has_price:
        return None, "Yahoo 查不到股價（代號可能錯，或不是可交易標的）"
    qt = (info.get("quoteType") or "").upper()
    en = " ".join((info.get("shortName") or info.get("longName") or "").split())
    if qt in _QUOTE_TYPE_INDUSTRY:
        industry, sector, source = _QUOTE_TYPE_INDUSTRY[qt], "", "manual"
    else:
        industry = translate_yfinance_industry(info.get("industry"), info.get("sector")) or ""
        sector = SECTOR_ZH.get(info.get("sector"), info.get("sector") or "")
        source = "yfinance" if industry else ""
    suffix = ticker.rsplit(".", 1)[-1] if "." in ticker else ""
    market = {"T": "JP", "HK": "HK", "KS": "KR", "DE": "DE", "SS": "CN", "SZ": "CN"}.get(suffix, "")
    if not market and qt in ("EQUITY", "ETF") and not ticker.startswith("^"):
        market = "US"
    zh = tm_zh.get(ticker, "")
    return dict(ticker=ticker, market=market, industry_code="", industry_name=industry, sector_name=sector,
                source=source, zh_name=zh, en_name=en,
                name_source="+".join(s for s in ("tickermap" if zh else "", "yfinance" if en else "") if s)), ""


class Command(BaseCommand):
    help = "把回測、情緒分數、小表用到但大表沒收錄的 ticker 補進 IndustryClassification（預設 dry run）"

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="實際寫入 summariesdb")

    def handle(self, *args, commit=False, **opts):
        existing = set(IndustryClassification.objects.using(DB).values_list("ticker", flat=True))
        refs, ref_assets, verified = _referenced_tickers()
        missing = sorted((t for t in refs if t not in existing), key=lambda t: -refs[t])
        self.stdout.write(f"三張表用到的 ticker：{len(refs)} 個，大表沒有的：{len(missing)} 個")
        if not missing:
            return

        official = _fetch_official()
        tm_zh = dict(TickerMap.objects.using(DB).exclude(zh_name="").values_list("ticker", "zh_name"))

        to_add: dict[str, dict] = {}
        notes, failed, suspect = [], [], []
        for t in missing:
            if t not in verified and _is_raw_passthrough(t, ref_assets.get(t, set())):
                suspect.append((t, refs[t], _sample_context(t)))
                continue
            if t.upper().endswith((".TW", ".TWO")):
                row, note = _classify_tw(t, official)
            else:
                row, note = _classify_other(t, tm_zh)
            if not row:
                failed.append((t, refs[t], note))
                continue
            if note:
                notes.append((t, refs[t], note))
            if row["ticker"] not in existing and row["ticker"] not in to_add:
                to_add[row["ticker"]] = row

        self.stdout.write(f"\n== 補進大表：{len(to_add)} 檔")
        for r in to_add.values():
            self.stdout.write(f"  {r['ticker']:12s} {r['industry_name'] or '(無產業)':12s} "
                              f"zh={r['zh_name'] or '-'}  en={r['en_name'] or '-'}")
        if notes:
            self.stdout.write(f"\n== 代號後綴寫錯（補的是正確的那一個；已存的紀錄之後用 resync 對齊）：{len(notes)} 個")
            for t, n, note in notes:
                self.stdout.write(f"  {t:12s} 用到 {n} 筆：{note}")
        if suspect:
            self.stdout.write(f"\n== 節目原話被原樣當成 ticker，要人工確認是不是這家公司（沒有補）：{len(suspect)} 個")
            for t, n, ctx in suspect:
                self.stdout.write(f"  {t:12s} 用到 {n} 筆：{ctx}")
        if failed:
            self.stdout.write(f"\n== 查不到、沒有補：{len(failed)} 個")
            for t, n, note in failed:
                self.stdout.write(f"  {t:12s} 用到 {n} 筆：{note}")

        if not commit:
            self.stdout.write(self.style.WARNING("\ndry run，沒有寫入資料庫。確認後加 --commit。"))
            return
        IndustryClassification.objects.using(DB).bulk_create([IndustryClassification(**r) for r in to_add.values()])
        # 新補的列如果有小表別名，順便連上外鍵（TickerMap.save() 會自動找大表同 ticker 那一列）
        linked = 0
        for tm in TickerMap.objects.using(DB).filter(classification__isnull=True, ticker__in=list(to_add)):
            tm.save(using=DB)
            linked += 1
        self.stdout.write(self.style.SUCCESS(f"\n已補進大表 {len(to_add)} 檔，小表連上 {linked} 筆"))
