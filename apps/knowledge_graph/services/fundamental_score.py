# apps/knowledge_graph/services/fundamental_score.py
"""
Layer 2 財務體質評分：F-score 式的檢查表（獲利、成長、資本結構、資產效率
四個面向），依風險分級（保守/均衡/積極）套用不同的通過比例門檻。

（Altman Z-score 曾經是這裡的信評替代品主力，試過純 Z-score、Z-score+槓桿
覆蓋率加權合成、0-100正規化、近4季平均EBIT/營收等好幾種變體，用實際有
TCRI/S&P信評的公司做 Spearman 排名相關係數驗證，結果最好的版本也只有
-0.345，加入台灣中小型股樣本後最新一次測到只剩 -0.082——幾乎跟真實信評的
排序沒有關係。核心問題是 Z-score 只看資產負債表比率，量不到公司規模、
產業地位、業務集中度這些真實信評會納入考量的因素，這是模型本身的天花板，
不是參數沒調好。最終決定整個拿掉，不用一個驗證證明不可靠的指標誤導使用者，
只保留 F-score——F-score 問的是「跟自己過去比、體質有沒有變差」，不是
「絕對信用等級多高」，是不同性質的問題，沒有被同樣的驗證推翻。）

設計原則（呼應前面討論過的理由，寫在這裡而不是散落在函式裡）：
  - 不用產業別的絕對數字門檻（例如「毛利率要 > 15%」），因為同一個絕對值
    在不同產業之間沒有可比性。改用「跟自己過去比」的方向性檢查——這是
    Piotroski F-Score 能跨產業使用的原因，這裡沿用同樣的邏輯。
  - 「營業現金流 ÷ 稅後淨利」是盈餘品質的紅旗指標，獨立設成硬性淘汰條件，
    不算進 F-score 的計分裡——帳面淨利如果沒有現金流佐證，後面算出來的
    任何分數都是建立在不可信的地基上，不該讓其他項目的高分把這個蓋過去。
  - F-score 的每一項檢查如果因為資料深度不足（例如 yfinance 只回溯 7 季，
    抓不到 3 年前同季資料）而無法計算，就從 max_score 排除，不會被當成
    「沒通過」硬算進失敗，也不會假裝通過——用比例（score/max_score）而不是
    固定分母比較不同候選，才不會因為資料來源不同（yfinance vs FinMind
    回溯深度不同）而系統性懲罰某一邊。
"""
from __future__ import annotations

from datetime import date

# 每項 F-score 檢查通過時，附一句白話解釋「這代表什麼意思」，給前端顯示用。
# 只在檢查通過（True）時才會被引用——沒通過或無法判斷的項目不呈現解釋，
# 避免使用者誤以為「有列出來」就是正面訊號。
CHECK_EXPLANATIONS = {
    "營業利益為正": "本業經營本身就能賺錢。",
    "毛利率較去年同期未惡化": "與去年同期相比，產品定價能力或成本控制沒有轉弱。",
    "營收年增為正": "生意規模比去年同期成長。",
    "EPS年增為正": "每股獲利比去年同期成長，股東實際分得的獲利在增加。",
    "三年營收CAGR為正": "近三年營收整體成長，中期業務規模較三年前擴大。",
    "EPS環比增加": "相較上一季賺得更多，代表短期每股獲利有所改善。",
    "負債比率未惡化": "與去年同期相比，總負債占總資產的比例沒有升高。",
    "資產週轉率未惡化": "與去年同期相比，公司運用資產創造營收的效率未下降。",
}

# 每項檢查對應的「判斷標準」白話文字，給前端財務驗證表格顯示用。全部都是
# 跟自己過去比（YoY/QoQ），不是跨產業的絕對數字門檻，只有「營業利益為正」
# 例外——賺不賺錢本身就是天然的絕對邊界（0），不是產業特定的武斷門檻。
CHECK_THRESHOLDS = {
    "營業利益為正": "> 0",
    "毛利率較去年同期未惡化": "≥ 去年同期",
    "營收年增為正": "> 去年同期",
    "EPS年增為正": "> 去年同期",
    "三年營收CAGR為正": "> 0%",
    "EPS環比增加": "> 上一季",
    "負債比率未惡化": "≤ 去年同期",
    "資產週轉率未惡化": "≥ 去年同期",
}

RISK_TIER_CONFIG = {
    "保守": {"f_score_ratio_min": 0.8},
    "均衡": {"f_score_ratio_min": 0.5},
    "積極": {"f_score_ratio_min": 0.25},
}


# ── 硬性淘汰：盈餘品質 ─────────────────────────────────────────────────────

def passes_earnings_quality_gate(latest: dict) -> bool:
    net_income = latest.get("net_income")
    ocf = latest.get("operating_cash_flow")
    if net_income is None or net_income <= 0:
        return False
    return ocf is not None and ocf >= 0.8 * net_income


# ── F-score 式檢查表 ─────────────────────────────────────────────────────

def _fmt_pct_change(new: float | None, old: float | None) -> str | None:
    """相對百分比變化，例如 +18.6%——用在營收/EPS/CAGR這類「絕對數值」的成長率。"""
    if new is None or old is None or old == 0:
        return None
    return f"{(new - old) / abs(old) * 100:+.1f}%"


def _fmt_pp_change(new: float | None, old: float | None) -> str | None:
    """百分點變化，例如 -1.2 個百分點——用在毛利率/負債比率/資產週轉率這類本身
    已經是比率的指標，比率的變化該看差幾個百分點，不是看比率的比率（會失真）。"""
    if new is None or old is None:
        return None
    return f"{(new - old) * 100:+.1f} 個百分點"


def _fmt_large_number(value: float | None) -> str | None:
    if value is None:
        return None
    abs_v = abs(value)
    sign = "-" if value < 0 else ""
    if abs_v >= 1e12:
        return f"{sign}{abs_v / 1e12:.2f}兆"
    if abs_v >= 1e8:
        return f"{sign}{abs_v / 1e8:.2f}億"
    if abs_v >= 1e4:
        return f"{sign}{abs_v / 1e4:.1f}萬"
    return f"{sign}{abs_v:.0f}"


def _gross_margin(period: dict) -> float | None:
    revenue = period.get("revenue")
    gross_profit = period.get("gross_profit")
    if not revenue or gross_profit is None:
        return None
    return gross_profit / revenue


def _find_same_quarter(quarterly: list[dict], year: int, month: int) -> dict | None:
    for p in quarterly:
        pe = p["fiscal_period_end"]
        if pe.year == year and pe.month == month:
            return p
    return None


def _previous_quarter(quarterly: list[dict]) -> dict | None:
    """
    緊接在最新一季之前的那一季（環比，不是年增）。quarterly 已經是新到舊排序，
    理論上 quarterly[1] 就是上一季，但實際資料可能中間缺一季（例如某季財報
    不完整被跳過），這種情況下 quarterly[1] 其實是「上上季」甚至更早，不是
    真正的環比對象，用日期間隔（120天內）驗證是不是真的緊鄰的一季，不是的話
    當作抓不到，不要拿不相鄰的兩季硬比。
    """
    if len(quarterly) < 2:
        return None
    prev = quarterly[1]
    gap_days = (quarterly[0]["fiscal_period_end"] - prev["fiscal_period_end"]).days
    return prev if gap_days <= 120 else None


def _leverage_ratio(period: dict) -> float | None:
    ta = period.get("total_assets")
    tl = period.get("total_liabilities")
    if not ta or tl is None:
        return None
    return tl / ta


def _asset_turnover(period: dict) -> float | None:
    ta = period.get("total_assets")
    revenue = period.get("revenue")
    if not ta or revenue is None:
        return None
    return revenue / ta


def compute_f_score(quarterly: list[dict], annual_revenue: list[dict]) -> dict:
    """
    回傳 {"score", "max_score", "checks": {name: {"pass", "actual"}}}。
    pass=None 代表資料不足以判斷該項，不計入 score 也不計入 max_score，
    actual 這種情況下也會是 None。actual 是給前端「財務驗證」表格顯示用的
    白話數值（例如 "+18.6%"），threshold 統一由 CHECK_THRESHOLDS 提供，
    不跟著每筆結果重複存。
    """
    latest = quarterly[0]
    pe = latest["fiscal_period_end"]
    yoy = _find_same_quarter(quarterly, pe.year - 1, pe.month)
    prev_q = _previous_quarter(quarterly)

    checks: dict[str, dict] = {}

    op_income = latest.get("operating_income")
    checks["營業利益為正"] = {
        "pass": None if op_income is None else op_income > 0,
        "actual": _fmt_large_number(op_income),
    }

    latest_gm = _gross_margin(latest)
    yoy_gm = _gross_margin(yoy) if yoy else None
    checks["毛利率較去年同期未惡化"] = {
        "pass": None if latest_gm is None or yoy_gm is None else latest_gm >= yoy_gm,
        "actual": _fmt_pp_change(latest_gm, yoy_gm),
    }

    yoy_revenue = yoy.get("revenue") if yoy else None
    latest_revenue = latest.get("revenue")
    checks["營收年增為正"] = {
        "pass": None if not yoy_revenue or latest_revenue is None else latest_revenue > yoy_revenue,
        "actual": _fmt_pct_change(latest_revenue, yoy_revenue),
    }

    yoy_eps = yoy.get("eps") if yoy else None
    latest_eps = latest.get("eps")
    checks["EPS年增為正"] = {
        "pass": None if yoy_eps is None or latest_eps is None else latest_eps > yoy_eps,
        "actual": _fmt_pct_change(latest_eps, yoy_eps),
    }

    cagr_pass, cagr_actual = None, None
    if annual_revenue:
        latest_year = annual_revenue[0]["year"]
        base = next((r for r in annual_revenue if r["year"] == latest_year - 3), None)
        if base and base["revenue"] and base["revenue"] > 0:
            cagr = (annual_revenue[0]["revenue"] / base["revenue"]) ** (1 / 3) - 1
            cagr_pass = cagr > 0
            cagr_actual = f"{cagr * 100:+.1f}%"
    checks["三年營收CAGR為正"] = {"pass": cagr_pass, "actual": cagr_actual}

    prev_eps = prev_q.get("eps") if prev_q else None
    checks["EPS環比增加"] = {
        "pass": None if prev_eps is None or latest_eps is None else latest_eps > prev_eps,
        "actual": _fmt_pct_change(latest_eps, prev_eps),
    }

    latest_leverage = _leverage_ratio(latest)
    yoy_leverage = _leverage_ratio(yoy) if yoy else None
    checks["負債比率未惡化"] = {
        "pass": None if latest_leverage is None or yoy_leverage is None else latest_leverage <= yoy_leverage,
        "actual": _fmt_pp_change(latest_leverage, yoy_leverage),
    }

    latest_turnover = _asset_turnover(latest)
    yoy_turnover = _asset_turnover(yoy) if yoy else None
    checks["資產週轉率未惡化"] = {
        "pass": None if latest_turnover is None or yoy_turnover is None else latest_turnover >= yoy_turnover,
        "actual": _fmt_pct_change(latest_turnover, yoy_turnover),
    }

    score = sum(1 for v in checks.values() if v["pass"] is True)
    max_score = sum(1 for v in checks.values() if v["pass"] is not None)
    return {"score": score, "max_score": max_score, "checks": checks}


# ── 主流程 ───────────────────────────────────────────────────────────────

def evaluate_candidate(ticker: str, as_of_date: date, risk_tier: str) -> dict:
    from .financial_data import get_financial_history

    history = get_financial_history(ticker, as_of_date)
    if not history["quarterly"]:
        return {"status": "unavailable", "data_source": None}

    quarterly = history["quarterly"]
    latest = quarterly[0]
    hard_gate_pass = passes_earnings_quality_gate(latest)
    f = compute_f_score(quarterly, history["annual_revenue"])
    config = RISK_TIER_CONFIG[risk_tier]
    f_ratio = (f["score"] / f["max_score"]) if f["max_score"] else 0.0

    reasons_failed = []
    if not hard_gate_pass:
        reasons_failed.append("營業現金流無法支撐帳面淨利，盈餘品質不通過")
    if f["max_score"] and f_ratio < config["f_score_ratio_min"]:
        reasons_failed.append("財務體質評分未達此風險等級門檻")

    passed_checks = [
        {"name": name, "explanation": CHECK_EXPLANATIONS.get(name, "")}
        for name, v in f["checks"].items() if v["pass"] is True
    ]

    # 財務驗證表格用：每一項不管通過與否都列出來（None 的項目跳過，資料
    # 不足以判斷的東西沒有數值可以呈現，列出來也只會是空白列）。
    detail_rows = [
        {
            "name": name,
            "pass": v["pass"],
            "actual": v["actual"],
            "threshold": CHECK_THRESHOLDS.get(name, ""),
            "explanation": CHECK_EXPLANATIONS.get(name, ""),
        }
        for name, v in f["checks"].items() if v["pass"] is not None
    ]

    return {
        "status": "pass" if not reasons_failed else "rejected",
        "data_source": latest["data_source"],
        "fiscal_period_end": latest["fiscal_period_end"].isoformat(),
        "f_score": f["score"],
        "f_score_max": f["max_score"],
        "f_score_checks": f["checks"],
        "f_score_passed_checks": passed_checks,
        "f_score_details": detail_rows,
        "hard_gate_pass": hard_gate_pass,
        "reasons_failed": reasons_failed,
    }


def annotate_basket_with_scores(basket: list[dict], risk_tier: str, as_of_date: date) -> list[dict]:
    """
    對 basket 裡每個候選做 Layer 2 評分，把結果附加到 candidate["layer2"]。
    status="rejected" 的候選會被移除（不符合這個風險等級的財務體質）；
    status="unavailable"（抓不到財務資料，例如外國標的沒有涵蓋）保留在
    basket 裡並誠實標註，不悄悄過濾掉。
    """
    from .financial_data import resolve_financial_ticker

    kept = []
    for c in basket:
        ticker = resolve_financial_ticker(c["node"])
        if not ticker:
            c["layer2"] = {"status": "unavailable", "data_source": None}
            kept.append(c)
            continue
        result = evaluate_candidate(ticker, as_of_date, risk_tier)
        result["ticker"] = ticker
        c["layer2"] = result
        if result["status"] != "rejected":
            kept.append(c)
    return kept
