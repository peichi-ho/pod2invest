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
    "營收年增為正": "> 去年同期",
    "三年營收CAGR為正": "> 0%",
    "EPS年增為正": "> 去年同期",
    "EPS環比增加": "> 上一季",
    "毛利率較去年同期未惡化": "≥ 去年同期",
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


# ── 報酬面：風險（上面的F-score）負責過關，這裡負責描述「這個候選長什麼
# 樣子」，兩者不合併成單一分數 ────────────────────────────────────────────
#
# 原本的設計是把成長／估值轉成 0-100 分數、依風險等級加權平均成一個
# reward_score 排序，但實測發現兩個問題：(1) 複合分數對使用者不直覺，
# 不知道「71.9分」代表什麼；(2) 估值（P/S比自己歷史便宜多少%）在多頭階段
# 幾乎所有候選都會撞到「比過去貴」，排序鑑別度形同虛設（實測力積電/三星/
# 聯電三檔近一年P/S全部大幅上漲，三檔的估值管道分數都是0）。
#
# 改成：風險（F-score通過比例）、成長（3個方向指標為正的個數）各自分成
# 3級，組合成「體質穩健・成長強勁」這種標籤——每個字都能直接連回使用者
# 看得到的具體數字，不是黑盒分數。估值變化（P/S）不進這個分類，只用原始
# 貴/便宜%呈現，因為「現在比自己貴/便宜多少」跟「體質好不好、成長強不強」
# 是不同維度的資訊，硬塞進同一組標籤只會讓組合暴增、失去可讀性。

_RISK_RANK = {"體質穩健": 0, "體質普通": 1, "體質偏弱": 2}
_GROWTH_RANK = {"成長強勁": 0, "成長溫和": 1, "成長有限": 2}


def classify_risk(f_score: int, f_score_max: int) -> str | None:
    """
    依F-score通過比例分三級，門檻是判斷值（不是實證出來的切點，跟
    RISK_TIER_CONFIG的通過門檻是兩套獨立的東西——這裡不管使用者查詢時選
    哪個風險型，用同一套固定門檻分類，只是描述「這個候選體質好不好」，
    不是決定它過不過關）。f_score_max=0（完全無法計算任何檢查項）時
    回傳None，不硬分類。
    """
    if not f_score_max:
        return None
    ratio = f_score / f_score_max
    if ratio >= 0.75:
        return "體質穩健"
    if ratio >= 0.40:
        return "體質普通"
    return "體質偏弱"


def classify_growth(*growth_values: float | None) -> str | None:
    """
    依「幾個成長指標是正的」分三級。用比例（正的個數／有資料的個數）
    而不是固定分母，是因為三年CAGR常常因為抓不到三年前資料而缺值——
    跟F-score同樣的邏輯，資料不足該從分母排除，不是當成沒通過。全部
    指標都缺資料時回傳None。
    """
    known = [v for v in growth_values if v is not None]
    if not known:
        return None
    ratio = sum(1 for v in known if v > 0) / len(known)
    if ratio >= 1.0:
        return "成長強勁"
    if ratio >= 0.5:
        return "成長溫和"
    return "成長有限"


def _revenue_yoy_pct(quarterly: list[dict]) -> float | None:
    """反映生意規模最近有沒有在擴大，是最直接但也最短期的成長訊號。"""
    latest = quarterly[0]
    pe = latest["fiscal_period_end"]
    yoy = _find_same_quarter(quarterly, pe.year - 1, pe.month)
    if not yoy:
        return None
    yoy_revenue, latest_revenue = yoy.get("revenue"), latest.get("revenue")
    if not yoy_revenue or latest_revenue is None:
        return None
    return (latest_revenue - yoy_revenue) / abs(yoy_revenue) * 100


def _eps_yoy_pct(quarterly: list[dict]) -> float | None:
    """扣掉成本費用後，股東實際分得的獲利有沒有變多——比營收成長更貼近
    股東，但單季數字容易被業外損益等因素放大波動。"""
    latest = quarterly[0]
    pe = latest["fiscal_period_end"]
    yoy = _find_same_quarter(quarterly, pe.year - 1, pe.month)
    if not yoy:
        return None
    yoy_eps, latest_eps = yoy.get("eps"), latest.get("eps")
    if yoy_eps in (None, 0) or latest_eps is None:
        return None
    return (latest_eps - yoy_eps) / abs(yoy_eps) * 100


def _revenue_cagr_3y_pct(annual_revenue: list[dict]) -> float | None:
    """拉長時間看整體年化成長，用來確認短期的成長是不是有結構性延續，
    不是單一季度的曇花一現。"""
    if not annual_revenue:
        return None
    latest_year = annual_revenue[0]["year"]
    base = next((r for r in annual_revenue if r["year"] == latest_year - 3), None)
    if not base or not base["revenue"] or base["revenue"] <= 0:
        return None
    cagr = (annual_revenue[0]["revenue"] / base["revenue"]) ** (1 / 3) - 1
    return cagr * 100


def _price_to_sales(period: dict) -> float | None:
    """
    市值 ÷ 當期營收——這裡的營收是「單季」，不是慣例的TTM（近四季）滾動
    營收，是刻意簡化：P/S在這裡只拿來跟公司自己的歷史P/S比，不是跨公司
    比較，只要前後每一期都用同一套「單季基準」，比較出來的相對便宜/昂貴
    程度仍然成立，不需要為了跟業界慣例一致而額外處理近四季加總、增加對
    連續四季資料完整度的要求。
    """
    price = period.get("price_at_period_end")
    shares = period.get("shares_outstanding")
    revenue = period.get("revenue")
    if price is None or not shares or not revenue or revenue <= 0:
        return None
    return (price * shares) / revenue


def _valuation_snapshot(quarterly: list[dict]) -> dict:
    """
    回傳 {"current_ps", "avg_ps", "change_pct"}——目前P/S、自己過去幾期
    平均P/S，以及兩者的差距%（正值＝貴、負值＝便宜）。change_pct不歸零
    ——之前歸零的版本讓「貴3倍」跟「貴70%」都變成同樣的0分，在多頭階段
    幾乎所有候選都會撞到這個天花板、完全失去鑑別度。這裡只回傳原始數字，
    不做任何評分，好壞留給使用者自己對照營收成長來判斷（見classify_
    valuation()的門檻用途說明——那也只是顯示分類，不是好壞判斷）。
    任一值算不出來時，整組回傳None。
    """
    empty = {"current_ps": None, "avg_ps": None, "change_pct": None}
    if not quarterly:
        return empty
    latest_ps = _price_to_sales(quarterly[0])
    if latest_ps is None or latest_ps <= 0:
        return empty
    historical = [v for p in quarterly[1:] if (v := _price_to_sales(p)) and v > 0]
    if not historical:
        return empty
    avg_ps = sum(historical) / len(historical)
    if avg_ps <= 0:
        return empty
    return {
        "current_ps": latest_ps,
        "avg_ps": avg_ps,
        "change_pct": (latest_ps - avg_ps) / avg_ps * 100,
    }


def classify_valuation(change_pct: float | None) -> str | None:
    """
    依「現在P/S比自己歷史平均貴/便宜多少%」分三級，純粹給畫面上的儀表/
    徽章用，不進風險×成長那組9宮格分類、不影響排序——「現在比自己貴/
    便宜多少」是跟體質好壞、成長強弱不同維度的資訊。門檻±10%是判斷值，
    之後可依實際分佈調整。
    """
    if change_pct is None:
        return None
    if change_pct > 10:
        return "相對偏高"
    if change_pct < -10:
        return "相對偏低"
    return "相對合理"


def _revenue_growth_over_valuation_baseline(quarterly: list[dict]) -> float | None:
    """
    跟_valuation_snapshot()用同一組季度當基準算營收成長了多少%——不是
    複用_revenue_yoy_pct()，那是跟去年同一季比，時間跨度跟估值變化（跟
    過去好幾季平均比）對不上，兩個數字放在一起比較會失真。這裡讓兩個
    數字用同一個基準期間，比較才公平。
    """
    if len(quarterly) < 2:
        return None
    latest_revenue = quarterly[0].get("revenue")
    historical_revenues = [p.get("revenue") for p in quarterly[1:] if p.get("revenue")]
    if latest_revenue is None or not historical_revenues:
        return None
    avg_revenue = sum(historical_revenues) / len(historical_revenues)
    if avg_revenue <= 0:
        return None
    return (latest_revenue - avg_revenue) / avg_revenue * 100


def _valuation_note(valuation_change: float | None, revenue_growth: float | None) -> str | None:
    """
    中性描述估值變化跟同期營收成長的相對大小，不下「這樣是好是壞」的
    判斷——好壞牽涉市場情緒、未來展望這些我們沒有資料能判斷的東西，
    只客觀陳述「這段期間的價格變化，財報數字解釋得了多少」。
    """
    if valuation_change is None or revenue_growth is None:
        return None
    if valuation_change > revenue_growth:
        return "估值漲幅高於同期營收成長，這段期間的價格變化，財報數字只解釋了一部分，其餘比較多反映市場預期"
    return "估值變化幅度接近或低於同期營收成長，這段期間的價格變化跟業績表現大致吻合"


# ── 主流程 ───────────────────────────────────────────────────────────────

def evaluate_candidate(ticker: str, as_of_date: date, risk_tier: str) -> dict:
    from .financial_data import get_financial_history

    history = get_financial_history(ticker, as_of_date)
    if not history["quarterly"]:
        return {"status": "unavailable", "data_source": None}

    quarterly = history["quarterly"]
    annual_revenue = history["annual_revenue"]
    latest = quarterly[0]
    hard_gate_pass = passes_earnings_quality_gate(latest)
    f = compute_f_score(quarterly, annual_revenue)
    config = RISK_TIER_CONFIG[risk_tier]
    f_ratio = (f["score"] / f["max_score"]) if f["max_score"] else 0.0

    revenue_yoy = _revenue_yoy_pct(quarterly)
    eps_yoy = _eps_yoy_pct(quarterly)
    cagr_3y = _revenue_cagr_3y_pct(annual_revenue)
    risk_label = classify_risk(f["score"], f["max_score"])
    growth_label = classify_growth(revenue_yoy, eps_yoy, cagr_3y)

    valuation = _valuation_snapshot(quarterly)
    valuation_change = valuation["change_pct"]
    valuation_label = classify_valuation(valuation_change)
    revenue_growth_baseline = _revenue_growth_over_valuation_baseline(quarterly)

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
        "risk_label": risk_label,
        "growth_label": growth_label,
        "combined_label": f"{risk_label}・{growth_label}" if risk_label and growth_label else None,
        "revenue_yoy_pct": round(revenue_yoy, 1) if revenue_yoy is not None else None,
        "eps_yoy_pct": round(eps_yoy, 1) if eps_yoy is not None else None,
        "cagr_3y_pct": round(cagr_3y, 1) if cagr_3y is not None else None,
        "valuation_change_pct": round(valuation_change, 1) if valuation_change is not None else None,
        "valuation_label": valuation_label,
        "current_ps": round(valuation["current_ps"], 1) if valuation["current_ps"] is not None else None,
        "avg_ps": round(valuation["avg_ps"], 1) if valuation["avg_ps"] is not None else None,
        "revenue_growth_baseline_pct": round(revenue_growth_baseline, 1) if revenue_growth_baseline is not None else None,
        "valuation_note": _valuation_note(valuation_change, revenue_growth_baseline),
    }


def annotate_basket_with_scores(
    basket: list[dict], risk_tier: str, as_of_date: date, on_progress=None,
) -> list[dict]:
    """
    對 basket 裡每個候選做 Layer 2 評分，把結果附加到 candidate["layer2"]。
    status="rejected" 的候選會被移除（不符合這個風險等級的財務體質）；
    status="unavailable"（抓不到財務資料，例如外國標的沒有涵蓋）保留在
    basket 裡並誠實標註，不悄悄過濾掉。

    on_progress：選填，簽章 (current, total) 的 callback，每處理完一個候選
    （不管抓不抓得到財務資料）就回報一次，供呼叫端即時顯示進度——這裡是
    整條 pipeline 唯一逐筆打外部 API（yfinance/FinMind）的階段，最慢。
    """
    from .financial_data import resolve_financial_ticker

    kept = []
    total = len(basket)
    for i, c in enumerate(basket):
        ticker = resolve_financial_ticker(c["node"])
        if not ticker:
            c["layer2"] = {"status": "unavailable", "data_source": None}
            kept.append(c)
        else:
            result = evaluate_candidate(ticker, as_of_date, risk_tier)
            result["ticker"] = ticker
            c["layer2"] = result
            if result["status"] != "rejected":
                kept.append(c)
        if on_progress:
            on_progress(i + 1, total)

    # 風險負責過關（上面已經做完），這裡負責排序——不算複合分數，直接用
    # 風險/成長兩個分類的名次排序，依查詢時選的風險型決定先比哪一個：
    # 積極型先比成長（強勁排最前）、保守型先比風險（穩健排最前）、均衡型
    # 兩個名次相加，兩邊都好的排最前。無法分類（缺資料）的排在最後，不是
    # 被排除，只是沒有資訊可以排序。
    def _sort_key(c: dict):
        layer2 = c["layer2"]
        risk_rank = _RISK_RANK.get(layer2.get("risk_label"))
        growth_rank = _GROWTH_RANK.get(layer2.get("growth_label"))
        unranked = risk_rank is None or growth_rank is None
        risk_rank = risk_rank if risk_rank is not None else len(_RISK_RANK)
        growth_rank = growth_rank if growth_rank is not None else len(_GROWTH_RANK)
        if risk_tier == "積極":
            primary = (growth_rank, risk_rank)
        elif risk_tier == "保守":
            primary = (risk_rank, growth_rank)
        else:
            primary = (risk_rank + growth_rank, risk_rank)
        return (unranked, primary)

    kept.sort(key=_sort_key)
    return kept
