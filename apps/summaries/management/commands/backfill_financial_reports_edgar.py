# apps/summaries/management/commands/backfill_financial_reports_edgar.py
"""
用 SEC EDGAR(XBRL companyfacts) 把「美股」的季度財報歷史補進 FinancialReportCache(summariesdb)。

為什麼需要這支：yfinance 的季報只回最近約 5 季，不夠算「5 年窗口起點與終點各一個近四季
EPS」(至少約 24 季)。EDGAR 的免費 API 有 2009 年起的單季資料，而且每筆都帶「實際公告日」。

跟 backfill_financial_reports_finmind.py(台股) 的差別：
  - 只處理 SEC 有登記的公司(20-F/IFRS 外國發行人如 ASML、TSM 季度資料不在這裡，會跳過)；
  - disclosure_date 用 SEC 的 filed(實際公告日)，不是估計值；
  - EPS 是「當時公告的原始值」，**不會**隨股票分割還原，所以這裡用 yfinance 的分割紀錄把
    「公告日早於分割日」的 EPS 除以分割比，讓整條序列在同一個股數基準上；
  - 10-K 沒有單季 Q4，用「全年 − Q1~Q3」推回來(EPS 不是嚴格可加，是近似；營收、淨利是精確的)；
  - 毛利：很多公司(Netflix、GE、Chevron…)在 XBRL 只申報營收跟營業成本，沒有 GrossProfit 概念，
    原本這些期別的毛利整個是空的。沒有 GrossProfit 時改用「營收 − 營業成本」推算(2026-10-21)。
    但推算不是每家都對：保險、醫療(CVS、UNH)、能源(CVX)的「營業成本」概念只涵蓋部分成本，
    減出來會高估 1~4 倍。所以每檔先拿推算值跟 yfinance 近幾季的毛利比對(gross_profit_check)，
    中位差距在 GP_MAX_DIFF 以內才採用，個別差太多的季度也不採用，沒得比對的不推算。

SEC 要求請求標頭帶可聯絡的 User-Agent，用 --user-agent 或環境變數 SEC_USER_AGENT 指定。

用法：
    python manage.py backfill_financial_reports_edgar AAPL NVDA --user-agent "Pod2invest 你的名字 you@example.com"
    python manage.py backfill_financial_reports_edgar --from-db          # DB 裡出現過的非台股代號
    python manage.py backfill_financial_reports_edgar NVDA --dry-run     # 只算並顯示，不寫入
"""
import os
import time
from datetime import date, timedelta
from typing import Optional

import requests
from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.summaries.models import FinancialReportCache, StockSentimentScore

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# 同一個欄位可能隨年份換 XBRL 概念名稱(例如營收)，依優先順序逐一補齊還沒有值的期間
# 虧損公司常用 BasicAndDiluted；10-K 跟 10-Q 用不同概念時，少了它 Q4 會推不出來(整季缺漏)
EPS_CONCEPTS = [
    "EarningsPerShareDiluted", "EarningsPerShareBasic", "EarningsPerShareBasicAndDiluted",
    # 最低優先：有些公司(例如改過標籤的)只有持續營業單位的每股盈餘；同一天公告時永遠輸給上面三個
    "IncomeLossFromContinuingOperationsPerDilutedShare", "IncomeLossFromContinuingOperationsPerBasicShare",
]
FLOW_CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "gross_profit": ["GrossProfit"],
    # 不存進資料庫，只拿來在沒有 GrossProfit 時推算毛利。刻意不收 CostsAndExpenses——那是
    # 全部營業成本加費用，拿來減會得到營業利益而不是毛利。
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
                        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
}
GP_MAX_DIFF = 0.10         # 推算毛利跟 yfinance 的容許差距(10%)
QUARTER_DAYS = (80, 100)    # 單季期間長度(13週約91天)
YEAR_DAYS = (350, 380)      # 全年期間長度(52/53週)
DUP_WINDOW_DAYS = 10        # 跟既有列的期末日差在這個範圍內，視為同一期
# 每股盈餘的合理上限：真實個股單季 EPS 很少超過幾百元(波克夏A股是例外，但這裡沒有A股)。
# 超過的幾乎都是單位標錯、股數極少的殼公司或「A股等值」，直接丟掉比放進去污染近四季加總好。
MAX_ABS_EPS = 1000.0
# 公告日離期末超過這麼多天，代表這一期是在「後來的年報／季報當比較欄位」才第一次出現在
# XBRL(原始公告沒被收錄或標籤不同)，真正的公告日早得多，改用推估值，不然回測窗口會把它擋掉
LATE_FIRST_APPEARANCE_DAYS = 130
QUARTER_DISCLOSURE_LAG = 45
ANNUAL_DISCLOSURE_LAG = 90


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _earliest_per_end(us_gaap: dict, concepts: list, unit: str, lo_hi: tuple, annual_only_10k: bool = False) -> dict:
    """
    {期末日: (起日, fact)}：同一個期末日，在「所有概念、所有公告」裡只留最早公告的那筆。

    為什麼要跨概念、跨起日挑最早：同一期的數字會在後面好幾年的 10-Q/10-K 當作「比較欄位」再出現，
    而且常是重列(recast)過的值。若依概念優先順序先挑，或同期末日有不同起日時先到先得，就可能挑到
    兩年後才公告的版本——disclosure_date 變得很晚，回測窗口起點會把它擋掉，近四季就湊不滿。
    同一天公告時，依概念順序(稀釋優先)決勝負。
    """
    best = {}
    for prio, concept in enumerate(concepts):
        for f in us_gaap.get(concept, {}).get("units", {}).get(unit, []):
            if "start" not in f or "end" not in f or "filed" not in f:
                continue
            if not lo_hi[0] <= _days(f["start"], f["end"]) <= lo_hi[1]:
                continue
            if annual_only_10k and not f.get("form", "").startswith("10-K"):
                continue
            if unit == "USD/shares" and abs(f.get("val", 0) or 0) > MAX_ABS_EPS:
                continue
            key = (f["filed"], prio)
            if f["end"] not in best or key < best[f["end"]][0]:
                best[f["end"]] = (key, f["start"], f)
    return {e: (s, f) for e, (_, s, f) in best.items()}


def quarterly_values(us_gaap: dict, concepts: list, unit: str, split_ratios: Optional[list] = None) -> dict:
    """
    回傳 {期末日(str): (值, 公告日(str))}，單季值；Q4 由全年推回。
    split_ratios: [(分割日 date, 比例)]，給的話把「公告日早於分割日」的值除以該比例(每股數據用)。
    """
    quarters = _earliest_per_end(us_gaap, concepts, unit, QUARTER_DAYS)
    years = _earliest_per_end(us_gaap, concepts, unit, YEAR_DAYS, annual_only_10k=True)

    def adj(val: float, filed: str) -> float:
        if not split_ratios:
            return val
        d = date.fromisoformat(filed)
        for sd, ratio in split_ratios:
            if sd > d:
                val /= ratio
        return val

    def disclosed(end_s: str, filed: str, lag_days: int) -> str:
        end_d, filed_d = date.fromisoformat(end_s), date.fromisoformat(filed)
        if (filed_d - end_d).days > LATE_FIRST_APPEARANCE_DAYS:
            return min(filed_d, end_d + timedelta(days=lag_days)).isoformat()
        return filed

    out = {e: (adj(f["val"], f["filed"]), disclosed(e, f["filed"], QUARTER_DISCLOSURE_LAG))
           for e, (s, f) in quarters.items()}

    # Q4 = 全年 − Q1~Q3(各自先換到同一股數基準再相減)
    for e, (s, f) in years.items():
        if e in out:
            continue
        in_year = [
            (qe, qs) for qe, (qs, _) in quarters.items()
            if qs >= (date.fromisoformat(s) - timedelta(days=5)).isoformat() and qe < e and _days(qe, e) > 20
        ]
        if len(in_year) != 3:
            continue
        q_sum = sum(out[qe][0] for qe, _ in in_year)
        out[e] = (adj(f["val"], f["filed"]) - q_sum, disclosed(e, f["filed"], ANNUAL_DISCLOSURE_LAG))
    if unit == "USD/shares":
        # 反向分割還原後才超過上限的(殼公司時期的舊資料)也一併丟掉
        out = {e: v for e, v in out.items() if abs(v[0]) <= MAX_ABS_EPS}
    return out


def gross_profit_check(ticker: str, revenue: dict, cost: dict) -> tuple[bool, set, str]:
    """
    推算毛利(營收 − 營業成本)能不能用：跟 yfinance 近幾季的 Gross Profit 逐季比對。
    回傳(這檔可不可以推算, 差距超過 GP_MAX_DIFF 而不能用的期末日, 說明文字)。
    yfinance 只有最近約 5 季，所以是用這幾季判斷整檔的成本概念對不對，更早的季度沿用同一個判斷。
    """
    import math
    import yfinance as yf

    try:
        qi = yf.Ticker(ticker).quarterly_income_stmt
    except Exception:
        return False, set(), "yfinance 讀取失敗，無法比對"
    if qi is None or "Gross Profit" not in qi.index:
        return False, set(), "yfinance 沒有毛利可比對"

    diffs, bad = [], set()
    for col in qi.columns:
        y = qi.loc["Gross Profit", col]
        if y is None or (isinstance(y, float) and math.isnan(y)) or not y:
            continue
        end = next((e for e in revenue if e in cost and abs((date.fromisoformat(e) - col.date()).days) <= DUP_WINDOW_DAYS), None)
        if end is None:
            continue
        diff = abs((revenue[end][0] - cost[end][0]) - float(y)) / abs(float(y))
        diffs.append(diff)
        if diff > GP_MAX_DIFF:
            bad.add(end)
    if not diffs:
        return False, set(), "跟 yfinance 沒有重疊的季度"
    median = sorted(diffs)[len(diffs) // 2]
    if median > GP_MAX_DIFF:
        return False, bad, f"推算值跟 yfinance 中位差距 {median:.0%}，成本概念不完整"
    return True, bad, f"跟 yfinance 比對 {len(diffs)} 季，中位差距 {median:.1%}"


def load_sec_ticker_map(session: requests.Session) -> dict:
    resp = session.get(SEC_TICKERS_URL, timeout=40)
    resp.raise_for_status()
    return {v["ticker"].upper(): int(v["cik_str"]) for v in resp.json().values()}


def is_fund(ticker: str) -> bool:
    """ETF／黃金信託等基金型標的有 SEC 檔案也有「EPS」，但那是資產價格漲跌，不是公司獲利，不能收。"""
    import yfinance as yf

    try:
        qt = (yf.Ticker(ticker).get_info() or {}).get("quoteType", "")
    except Exception:
        return False
    return qt in ("ETF", "MUTUALFUND")


def split_ratios_for(ticker: str) -> list:
    import yfinance as yf

    try:
        sp = yf.Ticker(ticker).splits
    except Exception:
        return []
    out = []
    for ts, ratio in sp.items():
        if ratio and ratio > 0 and ratio != 1:
            out.append((ts.date(), float(ratio)))
    return out


class Command(BaseCommand):
    help = "用 SEC EDGAR 補美股季度財報歷史進 FinancialReportCache"

    def add_arguments(self, parser):
        parser.add_argument("tickers", nargs="*", help="股票代號，例如 AAPL NVDA")
        parser.add_argument("--from-db", action="store_true",
                            help="改成取 DB 裡出現過的非台股代號(依被討論次數排序)；指數/商品/加密貨幣會自動略過")
        parser.add_argument("--top", type=int, default=0, help="--from-db 時只取前N檔")
        parser.add_argument("--user-agent", default=os.environ.get("SEC_USER_AGENT", ""),
                            help="SEC 要求的聯絡資訊標頭；也可用環境變數 SEC_USER_AGENT")
        parser.add_argument("--sleep", type=float, default=0.3, help="每檔之間暫停秒數(SEC 上限約每秒10次)")
        parser.add_argument("--dry-run", action="store_true", help="只抓取並顯示，不寫入資料庫")
        parser.add_argument("--skip-existing", action="store_true", help="略過已經有 EDGAR 資料的代號(斷線後接著跑用)")

    def handle(self, *args, **opts):
        ua = opts["user_agent"].strip()
        if not ua:
            self.stderr.write("請用 --user-agent 或環境變數 SEC_USER_AGENT 提供聯絡資訊(SEC 的使用規定)")
            return
        session = requests.Session()
        session.headers.update({"User-Agent": ua})

        tickers = [t.upper() for t in opts["tickers"]]
        if opts["from_db"]:
            rows = (StockSentimentScore.objects.using("summariesdb")
                    .values("ticker").annotate(n=Count("id")).order_by("-n"))
            picked = [r["ticker"] for r in rows
                      if not r["ticker"].upper().endswith((".TW", ".TWO"))
                      and not r["ticker"].startswith("^") and "=" not in r["ticker"] and "-USD" not in r["ticker"]]
            if opts["top"]:
                picked = picked[: opts["top"]]
            tickers = list(dict.fromkeys(tickers + picked))
        if not tickers:
            self.stderr.write("沒有可處理的代號（用代號列表或 --from-db）")
            return

        cik_map = load_sec_ticker_map(session)
        ok = total = 0
        gp_stats = {"has": 0, "derived": 0, "filled": 0}  # 有毛利的期數／其中推算的／資料庫原本沒有、這次補上的
        failed = []
        done = set()
        if opts["skip_existing"]:
            done = set(FinancialReportCache.objects.using("summariesdb")
                       .filter(data_source__contains="edgar").values_list("ticker", flat=True).distinct())
        for i, ticker in enumerate(tickers, 1):
            if ticker in done:
                continue
            cik = cik_map.get(ticker.replace("-", ".")) or cik_map.get(ticker)
            if cik is None:
                self.stderr.write(f"[{i}/{len(tickers)}] {ticker}: SEC 沒有這個代號（ETF、外國發行人或非美股），跳過")
                continue
            if is_fund(ticker):
                self.stderr.write(f"[{i}/{len(tickers)}] {ticker}: 是 ETF／基金，沒有公司獲利，跳過")
                continue
            try:
                resp = session.get(SEC_FACTS_URL.format(cik=cik), timeout=60)
                resp.raise_for_status()
                us_gaap = resp.json().get("facts", {}).get("us-gaap", {})
            except Exception as e:
                self.stderr.write(f"[{i}/{len(tickers)}] {ticker}: 抓取失敗 {e}")
                time.sleep(opts["sleep"])
                continue

            ratios = split_ratios_for(ticker)
            eps = quarterly_values(us_gaap, EPS_CONCEPTS, "USD/shares", ratios)
            if not eps:
                self.stderr.write(f"[{i}/{len(tickers)}] {ticker}: 沒有 us-gaap 單季 EPS（可能是 20-F/IFRS 公司），跳過")
                time.sleep(opts["sleep"])
                continue
            flows = {name: quarterly_values(us_gaap, c, "USD") for name, c in FLOW_CONCEPTS.items()}
            # 只有真的有期別缺 GrossProfit、又有營業成本可以推算時，才去跟 yfinance 比對
            gp_ok, gp_bad, gp_note = False, set(), ""
            if any(e not in flows["gross_profit"] and e in flows["cost_of_revenue"] and e in flows["revenue"]
                   for e in eps):
                gp_ok, gp_bad, gp_note = gross_profit_check(ticker, flows["revenue"], flows["cost_of_revenue"])

            existing = list(FinancialReportCache.objects.using("summariesdb").filter(ticker=ticker))
            n_new = n_merge = gp_filled_ticker = 0
            for end_s in sorted(eps):
                end = date.fromisoformat(end_s)
                values = {"eps": eps[end_s][0], "disclosure_date": date.fromisoformat(eps[end_s][1])}
                for name, series in flows.items():
                    if end_s in series:
                        values[name] = series[end_s][0]
                if "operating_income" in values:
                    values["ebit"] = values["operating_income"]
                cost = values.pop("cost_of_revenue", None)
                derived_gp = False
                if "gross_profit" not in values and cost is not None and values.get("revenue") \
                        and gp_ok and end_s not in gp_bad:
                    values["gross_profit"] = values["revenue"] - cost
                    derived_gp = True

                # 既有列(例如 yfinance 版)期末日只差幾天 → 同一期，補欄位而不是另開一列，
                # 否則「連續四季」檢查會因為兩列間隔太近而失敗
                near = next((r for r in existing if abs((r.fiscal_period_end - end).days) <= DUP_WINDOW_DAYS), None)
                if near is not None and near.fiscal_period_end != end:
                    end = near.fiscal_period_end
                if near is not None:
                    n_merge += 1
                else:
                    n_new += 1
                if values.get("gross_profit") is not None:
                    gp_stats["has"] += 1
                    gp_stats["derived"] += derived_gp
                    if near is None or near.gross_profit is None:
                        gp_stats["filled"] += 1
                        gp_filled_ticker += 1
                if not opts["dry_run"]:
                    defaults = dict(values)
                    defaults["data_source"] = "yfinance+edgar" if near is not None and near.data_source == "yfinance" else "edgar"
                    try:
                        FinancialReportCache.objects.using("summariesdb").update_or_create(
                            ticker=ticker, fiscal_period_end=end, defaults=defaults)
                    except Exception as e:      # 網路中斷等：記下這檔，繼續下一檔，最後列出來重跑
                        from django.db import connections
                        connections["summariesdb"].close()
                        self.stderr.write(f"[{i}/{len(tickers)}] {ticker}: 寫入失敗 {type(e).__name__}，稍後重跑")
                        failed.append(ticker)
                        break

            if failed and failed[-1] == ticker:
                time.sleep(opts["sleep"])
                continue
            total += len(eps)
            ok += 1
            ends = sorted(eps)
            seq = [eps[e][0] for e in ends]
            jumps = max((abs(b / a) if a else 0 for a, b in zip(seq, seq[1:]) if a > 0 and b > 0), default=0)
            self.stdout.write(
                f"[{i}/{len(tickers)}] {ticker}: {len(eps)} 期（{ends[0]} ~ {ends[-1]}），新增 {n_new}、合併既有 {n_merge}；"
                f"分割 {len(ratios)} 次；相鄰兩季 EPS 最大倍數 {jumps:.1f}x；毛利補上 {gp_filled_ticker} 期"
                + (f"（毛利推算：{'採用' if gp_ok else '不採用'}，{gp_note}）" if gp_note else "")
                + ("（dry-run，未寫入）" if opts["dry_run"] else "")
            )
            time.sleep(opts["sleep"])

        self.stdout.write(self.style.SUCCESS(
            f"完成：{ok}/{len(tickers)} 檔有資料，共 {total} 期" + ("（dry-run）" if opts["dry_run"] else "")
        ))
        self.stdout.write(
            f"毛利：{gp_stats['has']} 期有值（其中 {gp_stats['derived']} 期是營收−營業成本推算），"
            f"資料庫原本沒有、這次補上 {gp_stats['filled']} 期"
        )
        if failed:
            self.stderr.write("以下代號寫入失敗，請再跑一次(加 --skip-existing 會略過已完成的)：" + " ".join(failed))
