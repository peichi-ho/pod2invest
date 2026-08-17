# apps/summaries/services/backtesting.py
"""
回測相關工具：
  - resolve_ticker()       股票名稱 → Yahoo Finance ticker
  - calc_end_time()        timeframe_raw → 截止日期
  - create_backtesting_rows()  outlook_calls → 寫入 BacktestingRecord
"""
import re
from datetime import date, timedelta
from typing import Optional

from dateutil.relativedelta import relativedelta


# ── Timeframe → 截止日 ────────────────────────────────────────────────────────

def calc_end_time(timeframe_raw: str, start: date) -> Optional[date]:
    """
    把 LLM 產生的 timeframe_raw 換算成截止日。
    具體年份/日期允許最多 2 年；相對描述最多 1 年；超過上限回傳 None。
    """
    import calendar as _cal
    tf = (timeframe_raw or "").strip()
    tf_l = tf.lower()

    start_year = start.year
    next_year = start_year + 1
    abs_max = start + relativedelta(years=2)   # 具體日期上限（2年）
    rel_max = start + relativedelta(years=1)   # 相對描述上限（1年）

    def _clamp(d: date, limit: date = abs_max) -> Optional[date]:
        return d if start < d <= limit else None

    def _month_end(year: int, month: int) -> date:
        return date(year, month, _cal.monthrange(year, month)[1])

    # ── 1. 具體年份：2025 / 2026年 ───────────────────────────────────────────
    m = re.match(r"^(20\d{2})年?$", tf)
    if m:
        return _clamp(date(int(m.group(1)), 12, 31))

    # 2027年5月 / 2027年5月底
    m = re.match(r"(20\d{2})年(\d{1,2})月", tf)
    if m:
        return _clamp(_month_end(int(m.group(1)), int(m.group(2))))

    # 年份範圍：2027-2028 / 2027 / 2028（斜線或連接符）→ 取前者
    m = re.match(r"(20\d{2})\s*[－\-/~／～]\s*(20\d{2})", tf)
    if m:
        return _clamp(date(int(m.group(1)), 12, 31))

    # ── 2. 明後年 ────────────────────────────────────────────────────────────
    if "明後年" in tf:
        return _clamp(start + relativedelta(years=2))

    # ── 3. 明年系列 ──────────────────────────────────────────────────────────
    if "明年" in tf:
        if "上半年" in tf or "前半年" in tf:
            return _clamp(date(next_year, 6, 30))
        if "下半年" in tf or "後半年" in tf:
            return _clamp(date(next_year, 12, 31))
        for q, mo in [("Q1", 3), ("Q2", 6), ("Q3", 9), ("Q4", 12),
                      ("第一季", 3), ("第二季", 6), ("第三季", 9), ("第四季", 12)]:
            if q in tf:
                return _clamp(_month_end(next_year, mo))
        m = re.search(r"(\d{1,2})月", tf)
        if m:
            return _clamp(_month_end(next_year, int(m.group(1))))
        return _clamp(date(next_year, 12, 31))   # 明年（一般）→ 明年底

    # ── 4. 今年 / 年底 / 年內 / 全年 系列 ───────────────────────────────────
    if any(k in tf for k in ("年底", "今年底", "年底前", "年內", "全年")):
        return _clamp(date(start_year, 12, 31))
    if any(k in tf for k in ("今年年中", "年中")):
        return _clamp(date(start_year, 6, 30))
    if "今年" in tf:
        return _clamp(date(start_year, 12, 31))

    # ── 5. 農曆年後 ──────────────────────────────────────────────────────────
    if "農曆年後" in tf:
        # 農曆年約 1-2 月，農曆年後約 start + 2 個月作估算
        return _clamp(start + relativedelta(months=2), limit=rel_max)

    # ── 6. 季度 ──────────────────────────────────────────────────────────────
    # 下一季
    if "下一季" in tf or "下季" in tf:
        cur_q = (start.month - 1) // 3          # 0~3
        nxt_q = (cur_q + 1) % 4
        nxt_q_end_mo = [3, 6, 9, 12][nxt_q]
        nxt_q_year = start_year if nxt_q > cur_q else next_year
        return _clamp(_month_end(nxt_q_year, nxt_q_end_mo))

    # 本季
    if "本季" in tf:
        cur_q = (start.month - 1) // 3
        q_end_mo = [3, 6, 9, 12][cur_q]
        c = _month_end(start_year, q_end_mo)
        return _clamp(c if c > start else _month_end(next_year, q_end_mo))

    # Q1/Q2/Q3/Q4 / 第N季（不含明年）
    for q_key, mo in [("Q4", 12), ("第四季", 12),
                      ("Q3", 9),  ("第三季", 9),
                      ("Q2", 6),  ("第二季", 6),
                      ("Q1", 3),  ("第一季", 3)]:
        if q_key in tf:
            candidate = _month_end(start_year, mo)
            if candidate <= start:
                candidate = _month_end(next_year, mo)
            return _clamp(candidate)

    # ── 7. 上 / 下半年 ───────────────────────────────────────────────────────
    if "上半年" in tf:
        c = date(start_year, 6, 30)
        return _clamp(c if c > start else date(next_year, 6, 30))
    if "下半年" in tf:
        c = date(start_year, 12, 31)
        return _clamp(c if c > start else date(next_year, 12, 31))

    # ── 8. 具體月份 ──────────────────────────────────────────────────────────
    # N月以後 / N月後
    m = re.match(r"^(\d{1,2})月(?:以後|後|起)", tf)
    if m:
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            c = _month_end(start_year, mo)
            if c <= start:
                c = _month_end(next_year, mo)
            return _clamp(c)

    # 9月15號那個禮拜 → 該週五
    m = re.search(r"(\d{1,2})月(\d{1,2})號?那個禮拜", tf)
    if m:
        mo, day = int(m.group(1)), int(m.group(2))
        try:
            c = date(start_year, mo, day)
            c += timedelta(days=(4 - c.weekday()) % 7)   # 週五
            if c <= start:
                c = date(next_year, mo, day)
                c += timedelta(days=(4 - c.weekday()) % 7)
            return _clamp(c)
        except ValueError:
            pass

    # 中文數字月份：五月份、八月份
    _CN_NUM = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,
               "七":7,"八":8,"九":9,"十":10,"十一":11,"十二":12}
    m = re.match(r"^([一二三四五六七八九十]+)月份?$", tf)
    if m:
        mo = _CN_NUM.get(m.group(1))
        if mo:
            c = _month_end(start_year, mo)
            if c <= start:
                c = _month_end(next_year, mo)
            return _clamp(c)

    # 跨年月份範圍：11月到4月 → 取終點
    m = re.match(r"(\d{1,2})[月、，,]\s*(\d{1,2})月?\s*(?:到|至|-|～)\s*(\d{1,2})月", tf)
    if not m:
        m = re.match(r"(\d{1,2})月?\s*(?:到|至|[-～])\s*(\d{1,2})月", tf)
        if m:
            end_mo = int(m.group(2))
            c = _month_end(start_year, end_mo)
            if c <= start:
                c = _month_end(next_year, end_mo)
            return _clamp(c)

    # N月份 suffix（8月份、9月份）
    m = re.match(r"^(\d{1,2})月份$", tf)
    if m:
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            c = _month_end(start_year, mo)
            if c <= start:
                c = _month_end(next_year, mo)
            return _clamp(c)

    m = re.match(r"^(\d{1,2})月$", tf)
    if m:
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            c = _month_end(start_year, mo)
            if c <= start:
                c = _month_end(next_year, mo)
            return _clamp(c)

    # ── 9. 相對描述（上限 rel_max = 1 年）────────────────────────────────────
    def _rel(delta) -> Optional[date]:
        return _clamp(start + delta, limit=rel_max)

    # 多月份列舉：11、12月 → 取最後一個月
    m = re.search(r"(\d{1,2})[月、，,]+(\d{1,2})月", tf)
    if m:
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            c = _month_end(start_year, mo)
            if c <= start:
                c = _month_end(next_year, mo)
            return _clamp(c)

    # N個月
    m = re.match(r"^(\d+)個月$", tf)
    if m:
        return _rel(relativedelta(months=int(m.group(1))))

    # X到Y個月 / X至Y個月 → 取平均
    m = re.search(r"(\d+)[到至](\d+)個月", tf)
    if m:
        avg = (int(m.group(1)) + int(m.group(2))) // 2
        return _rel(relativedelta(months=avg))

    # 下個禮拜 / 下禮拜
    if any(k in tf for k in ("下個禮拜", "下禮拜")):
        return _clamp(start + timedelta(weeks=1), limit=rel_max)

    # 本週 → 當週五
    if "本週" in tf:
        days_to_fri = (4 - start.weekday()) % 7 or 7
        return _clamp(start + timedelta(days=days_to_fri), limit=rel_max)

    # 多年（超過可評估範圍）→ None
    if any(k in tf_l for k in ("未來幾年", "未來兩三年", "未來3-5年", "未來五年",
                                "十幾年", "幾年", "接下來一段時間", "一段時間",
                                "退休後", "下一個世代", "後面")):
        return None
    if any(k in tf_l for k in ("未來兩年", "未來2年", "兩年", "2年")):
        return _clamp(start + relativedelta(years=2))
    if any(k in tf_l for k in ("未來5季", "未來五季")):
        return _clamp(start + relativedelta(months=15))
    if any(k in tf_l for k in ("未來四季", "下個財年度", "未來一年", "未來1年", "一年")):
        return _rel(relativedelta(years=1))
    # 長期
    if any(k in tf_l for k in ("長期", "長線", "中長線", "long-term", "long", "每年")):
        return _rel(relativedelta(years=1))
    # 中期
    if any(k in tf_l for k in ("數個月到半年", "短期至中期", "短中期", "中短期")):
        return _rel(relativedelta(months=4))
    if any(k in tf_l for k in ("中期", "半年", "未來")):
        return _rel(relativedelta(months=6))
    # 短期
    if any(k in tf_l for k in ("短期", "短線", "一陣子")):
        return _rel(relativedelta(months=3))

    return None  # 無法解析


# ── 股票名稱 → ticker ─────────────────────────────────────────────────────────

def _rule_based_ticker(asset: str) -> Optional[str]:
    """第一層：規則直判，不查資料庫。"""
    a = asset.strip()

    # 純數字台股代號
    if re.match(r"^\d{4,6}$", a):
        return f"{a}.TW"

    # ── 指數關鍵字 ────────────────────────────────────────────────────────────
    INDEX_MAP = {
        ("台股加權", "加權指數", "大盤", "台北股市", "台股"): "^TWII",
        ("那斯達克", "Nasdaq", "nasdaq", "納斯達克", "納指"): "^IXIC",
        ("S&P500", "S&P 500", "S&P", "標準普爾", "標普", "標普500"): "^GSPC",
        ("費半", "費城半導體指數", "費城半導體"): "^SOX",
        ("道瓊",): "^DJI",
        ("羅素",): "^RUT",
        ("上證", "滬指"): "000001.SS",
        ("櫃買指數", "OTC", "上櫃指數", "櫃買"): "^TWOTCI",
        ("Stoxx 600", "stoxx600"): "^STOXX50E",
    }
    for keys, ticker in INDEX_MAP.items():
        if any(k in a for k in keys):
            return ticker

    # ── 大宗商品 / 外匯 ───────────────────────────────────────────────────────
    COMMODITY_MAP = {
        ("黃金",):               "GC=F",
        ("原油", "油價", "美油", "布油"): "CL=F",
        ("白銀",):               "SI=F",
        ("天然氣",):             "NG=F",
        ("日圓", "日元"):        "JPY=X",
        ("美元指數",):           "DX=F",
        ("比特幣",):             "BTC-USD",
        ("白金",):               "PL=F",
        ("石油",):               "CL=F",
    }
    for keys, ticker in COMMODITY_MAP.items():
        if any(k in a for k in keys):
            return ticker

    # ── 知名公司中文 / 別名 對應 ─────────────────────────────────────────────
    ALIAS_MAP = {
        # ── 美股：科技大廠 ──
        "微軟":    "MSFT",  "Microsoft":  "MSFT",
        "蘋果":    "AAPL",  "Apple":      "AAPL",
        "谷歌":    "GOOGL", "Google":     "GOOGL", "Alphabet": "GOOGL",
        "亞馬遜":  "AMZN",  "Amazon":     "AMZN",
        "臉書":    "META",  "Facebook":   "META",
        "輝達":    "NVDA",  "Nvidia":     "NVDA",  "nvidia": "NVDA",
        "英特爾":  "INTC",  "Intel":      "INTC",
        "超微":    "AMD",
        "高通":    "QCOM",  "Qualcomm":   "QCOM",
        "博通":    "AVGO",  "Broadcom":   "AVGO",
        "特斯拉":  "TSLA",  "Tesla":      "TSLA",
        "甲骨文":  "ORCL",  "Oracle":     "ORCL",
        "思科":    "CSCO",  "Cisco":      "CSCO",
        "IBM":     "IBM",
        "戴爾":    "DELL",  "Dell":       "DELL",
        "惠普":    "HPE",   "HP":         "HPE",
        "Seagate": "STX",   "希捷":       "STX",
        "美光":    "MU",    "Micron":     "MU",
        "應用材料": "AMAT", "Applied Materials": "AMAT",
        "科林研發": "LRCX", "Lam Research": "LRCX",
        "科磊":    "KLAC",  "KLA":        "KLAC",
        "恩智普":  "NXPI",  "恩智浦":     "NXPI",  "NXP": "NXPI",
        "艾斯摩爾": "ASML", "艾司摩爾":   "ASML",
        "阿里巴巴": "BABA", "Alibaba":    "BABA",
        "網飛":    "NFLX",  "Netflix":    "NFLX",
        "Palantir": "PLTR",
        "禮來":    "LLY",   "Eli Lilly":  "LLY",
        "波克夏":  "BRK-B",
        "馬菲爾":  "MRVL",  "Marvell":    "MRVL",
        "亞德諾":  "ADI",   "Analog Devices": "ADI",
        "ExxonMobil": "XOM","艾克森":     "XOM",
        "開拓重工": "CAT",  "Caterpillar": "CAT",
        "伊頓":    "ETN",   "Eaton":      "ETN",
        "聯合健康集團": "UNH", "聯合健康": "UNH",
        "Hynix":   "000660.KS",
        "MediaTek": "2454.TW",
        # ── 日股 ──
        "瑞薩":       "6723.T",
        "川崎重工":   "7012.T",
        "三菱重工":   "7011.T",
        "三菱電機":   "6503.T",
        "東京威力可創": "8035.T",
        # ── 台股：半導體 / 電子 ──
        "台積電":  "2330.TW",
        "聯發科":  "2454.TW",
        "日月光":  "3711.TW",
        "旺宏":    "2337.TW",
        "瑞昱":    "2379.TW",
        "矽力":    "6415.TW",
        "天鈺":    "4961.TW",
        "晶豪科":  "3006.TW",
        "南茂":    "8150.TW",
        "力成":    "6239.TW",
        "宏達電":  "2498.TW",
        "臻鼎":    "4958.TW",  "臻鼎KY": "4958.TW", "臻鼎-KY": "4958.TW", "臻鼎KY": "4958.TW",
        "玉晶光":  "3406.TW",
        "中磊":    "5388.TW",
        "啟碁":    "6285.TW",
        "立積":    "4968.TW",
        "神盾":    "6462.TW",
        "茂達":    "6138.TW",
        "系微":    "6203.TW",
        "宇瞻":    "8271.TW",
        "創見":    "2451.TW",
        "訊芯":    "6251.TW",
        "昆盈":    "2365.TW",
        "耀華":    "2367.TW",
        "廣明":    "6188.TW",
        "亞光":    "3019.TW",
        "十銓":    "3567.TW",
        "宜鼎":    "5289.TW",
        "仲琦":    "2419.TW",
        # ── 台股：科技硬體 ──
        "鴻海":    "2317.TW",
        "廣達":    "2382.TW",
        "台達電":  "2308.TW",
        "鴻準":    "2354.TW",
        "勤誠":    "8210.TW",
        "弘塑":    "3131.TW",
        "迅得":    "6438.TW",
        "嘉澤":    "3533.TW",
        "致茂":    "2360.TW",
        "穎崴":    "6515.TW",
        "豐達科":  "5288.TW",
        "羅昇":    "8374.TW",
        "加百裕":  "3323.TW",
        "光寶":    "2301.TW",
        "智易":    "3596.TW",
        "中環":    "2323.TW",
        "廣宇":    "2328.TW",
        "麥克":    "2158.TW",
        # ── 台股：電子通路 / 其他科技 ──
        "伊雲谷":  "6689.TW",
        # ── 台股：傳統 / 材料 ──
        "台塑化":  "6505.TW",
        "台化":    "1326.TW",
        "台塑":    "1301.TW",
        "南亞":    "1303.TW",
        "台聚":    "1304.TW",
        "亞聚":    "1308.TW",
        "國喬":    "1312.TW",
        "台肥":    "1722.TW",
        "大同":    "2371.TW",
        "台船":    "2208.TW",
        "上銀":    "2049.TW",
        "遠東新":  "1402.TW",
        "巨大":    "9921.TW",
        "美利達":  "9914.TW",
        "中鼎":    "9933.TW",
        "桂盟":    "5306.TW",
        "台特化":  "4746.TW",
        "可寧衛":  "8422.TW",
        "崑鼎":    "6803.TW",
        "康那香":  "9919.TW",
        "聖暉":    "6051.TW",
        "上緯投控": "3708.TW",
        "宇隆":    "2233.TW",
        "台聯":    "1304.TW",
        # ── 台股：金融 ──
        "國泰金":  "2882.TW",
        "富邦金":  "2881.TW",
        "玉山金":  "2884.TW",
        "兆豐金":  "2886.TW",
        "台新金":  "2887.TW",
        "新光金":  "2888.TW",
        "中信金":  "2891.TW",
        "開發金":  "2883.TW",
        "群益證":  "6005.TW",
        # ── 台股：航運 ──
        "華航":    "2610.TW",
        "長榮航":  "2618.TW",
        "虎航":    "6757.TW",  "台灣虎航": "6757.TW",
        "星宇航空": "2646.TW",
        # ── 台股：生技 / 醫療 ──
        "東洋":    "4105.TW",
        "康霈":    "6919.TW",
        "神隆":    "1789.TW",
        "柏文":    "8462.TW",
        "雄獅":    "2731.TW",
        # ── 台股：顯示 / 光電 ──
        "元太":    "8069.TW",
        "群創":    "3481.TW",
        "瑞儀":    "6176.TW",
        # ── 台股：高力（散熱）──
        "高力":    "8996.TW",
        # ── 台股：金融 / 其他 ──
        "統一超商": "2912.TW",
        "貿聯":    "3441.TW",  "貿聯-KY": "3441.TW",  "貿聯KY": "3441.TW",
        "茂林-KY": "4935.TW",
        "華城":    "1519.TW",
        "新日興":  "3376.TW",
        # ── 其他可識別 ──
        "臺光電":  "2383.TW",
        "和大":    "1536.TW",
        "鈺創":    "5351.TW",
        "森崴能源": "6806.TW",
        "櫻花":    "9911.TW",
        "JPP-KY":  "6545.TW",
        "升達科":  "8072.TW",
        "MPMaterial": "MP",
        "Confluent": "CFLT",
        "Lulu Lemon": "LULU",  "lululemon": "LULU",
        "Zebra":   "ZBRA",
        "NCSoft":  "036570.KS",
        "聯想":    "0992.HK",
        "中鋼特":  "2002.TW",
        "元大":    "2885.TW",  # 元大金控（最常見）
        "華通":    "2313.TW",
        "皇昌":    "2543.TW",
        "雷虎":    "8033.TW",
        "精銳":    "6550.TW",
        "波若威":  "3163.TW",
        "志強":    "6819.TW",
        "台灣虎航": "6757.TW",
        "亞航":    "2630.TW",  # 亞洲航空
    }
    if a in ALIAS_MAP:
        return ALIAS_MAP[a]
    a_lower = a.lower()
    for k, v in ALIAS_MAP.items():
        if k.lower() == a_lower:
            return v

    # ── ETF 代號（含 B 結尾債券 ETF）────────────────────────────────────────
    if re.match(r"^\d{5}[BbRr]$", a):
        return f"{a}.TW"

    # 已是英文 ticker（純大寫字母，最多 6 碼）。
    # 放在別名對照表之後才判斷，避免像 NVIDIA 這種剛好全大寫又<=6碼的
    # 公司名被誤判成合法ticker而跳過別名/TickerMap查詢。
    if re.match(r"^[A-Z]{1,6}$", a):
        return a

    return None


def resolve_ticker(asset: str) -> str:
    """
    股票名稱 → Yahoo Finance ticker。
    查不到回傳空字串（後續標記 skip）。
    """
    # 第一層：規則
    t = _rule_based_ticker(asset)
    if t:
        return t

    # 第二層：TickerMap 資料庫
    try:
        from apps.summaries.models import TickerMap
        row = TickerMap.objects.using("summariesdb").filter(asset_name=asset).first()
        if row:
            return row.ticker
    except Exception:
        pass

    return ""  # 無法解析


# ── 建立回測列 ────────────────────────────────────────────────────────────────

def create_backtesting_rows(
    *,
    summary_record,
    episode_id: Optional[int],
    outlook_calls: list[dict],
    published_at,          # datetime 或 date，可為 None
):
    """
    每筆 outlook_call（有 timeframe）建一列 BacktestingRecord。
    published_at=None 或 end_time 算不出來 → result=skip。
    """
    from apps.summaries.models import BacktestingRecord

    if not outlook_calls:
        return

    # published_at 轉成 date
    if published_at is None:
        start = None
    elif hasattr(published_at, "date"):
        start = published_at.date()
    else:
        start = published_at  # 已是 date

    rows = []
    for call in outlook_calls:
        tf_raw = (call.get("timeframe") or "").strip()
        if not tf_raw or tf_raw.lower() in ("null", "none"):
            continue  # 無 timeframe，不做回測

        ticker = resolve_ticker(call.get("asset", ""))

        # 計算截止日
        if start:
            end_time = calc_end_time(tf_raw, start)
        else:
            end_time = None

        # 無法解析 ticker 或 end_time → skip
        result = BacktestingRecord.RESULT_PENDING
        if not ticker or not end_time:
            result = BacktestingRecord.RESULT_SKIP

        rows.append(BacktestingRecord(
            summary=summary_record,
            episode_id=episode_id,
            asset=call.get("asset", ""),
            ticker=ticker,
            direction=call.get("direction", ""),
            timeframe_raw=tf_raw,
            thesis=call.get("thesis", ""),
            evidence_quote=call.get("evidence_quote", ""),
            evidence_timestamps=call.get("evidence_timestamps") or [],
            target_price=None,          # 目前 LLM 不輸出明確目標價
            start_time=start,
            end_time=end_time,
            result=result,
        ))

    if rows:
        BacktestingRecord.objects.using("summariesdb").bulk_create(rows)
