# apps/summaries/services/industry_classification.py
"""
產業分類邏輯，方法完全比照 apps/assets/services/industry_benchmarks.py 跟
industry_zh.py(個股詳情頁既有的分類方式，2026-09-30團隊決定統一採用)。

刻意獨立成一份、不直接 import apps.assets 底下的模組——那個app目前還沒合併進這個
checkout(在GitHub上游，本機還沒pull)，等之後真的合併時，這裡兩份重複的常數/邏輯
再考慮要不要合併成同一份，不要在合併時機不確定的狀態下先建立跨app依賴。

分類優先順序：
  1. 台股：TWSE官方產業別代碼(t187ap03_L開放API)，34種官方分類，權威資料來源
  2. 查不到才用yfinance：.info 的 sector/industry(Yahoo自家分類法)，翻譯成中文
"""
import re
from datetime import datetime, timedelta

import requests

_TWSE_COMPANY_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
_TPEX_COMPANY_INFO_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
_CACHE_TTL_SECONDS = 6 * 3600

# TWSE 官方產業別代碼 → 中文名稱，跟 apps/assets/services/industry_benchmarks.py
# 的 INDUSTRY_NAMES 保持一致(已用代表性公司驗證過)。
TWSE_INDUSTRY_NAMES: dict[str, str] = {
    '01': '水泥工業', '02': '食品工業', '03': '塑膠工業', '04': '紡織纖維',
    '05': '電機機械', '06': '電器電纜', '08': '玻璃陶瓷', '09': '造紙工業',
    '10': '鋼鐵工業', '11': '橡膠工業', '12': '汽車工業', '14': '建材營造',
    '15': '航運業', '16': '觀光餐旅', '17': '金融保險業', '18': '貿易百貨',
    '20': '其他', '21': '化學工業', '22': '生技醫療業', '23': '油電燃氣業',
    '24': '半導體業', '25': '電腦及週邊設備業', '26': '光電業', '27': '通信網路業',
    '28': '電子零組件業', '29': '電子通路業', '30': '資訊服務業', '31': '其他電子業',
    '35': '綠能環保', '36': '數位雲端', '37': '運動休閒', '38': '居家生活',
    '91': '存託憑證',
}

# yfinance sector 英文 → 中文，跟 apps/assets/services/industry_zh.py 的 SECTOR_ZH 一致。
SECTOR_ZH: dict[str, str] = {
    "Basic Materials": "原物料",
    "Communication Services": "通訊服務",
    "Consumer Cyclical": "非必需消費",
    "Consumer Defensive": "必需消費",
    "Energy": "能源",
    "Financial Services": "金融服務",
    "Healthcare": "醫療保健",
    "Industrials": "工業",
    "Real Estate": "不動產",
    "Technology": "科技",
    "Utilities": "公用事業",
}

# yfinance industry 英文 → 中文，跟 apps/assets/services/industry_zh.py 的 INDUSTRY_ZH 一致。
INDUSTRY_ZH: dict[str, str] = {
    "Agricultural Inputs": "農業投入", "Building Materials": "建材", "Chemicals": "化學",
    "Specialty Chemicals": "特用化學", "Lumber & Wood Production": "木材",
    "Paper & Paper Products": "造紙", "Aluminum": "鋁", "Copper": "銅",
    "Other Industrial Metals & Mining": "其他工業金屬與礦業", "Gold": "黃金", "Silver": "白銀",
    "Other Precious Metals & Mining": "其他貴金屬與礦業", "Coking Coal": "煉焦煤", "Steel": "鋼鐵",
    "Advertising Agencies": "廣告代理", "Publishing": "出版", "Broadcasting": "廣播電視",
    "Entertainment": "娛樂", "Internet Content & Information": "網路內容與資訊",
    "Electronic Gaming & Multimedia": "電子遊戲與多媒體", "Telecom Services": "電信服務",
    "Apparel Manufacturing": "成衣製造", "Apparel Retail": "成衣零售",
    "Auto & Truck Dealerships": "汽車經銷", "Auto Manufacturers": "汽車製造",
    "Auto Parts": "汽車零件", "Department Stores": "百貨公司",
    "Footwear & Accessories": "鞋類與配件", "Furnishings, Fixtures & Appliances": "家具家飾與家電",
    "Gambling": "博弈", "Home Improvement Retail": "居家修繕零售", "Internet Retail": "網路零售",
    "Leisure": "休閒", "Lodging": "住宿", "Luxury Goods": "精品",
    "Packaging & Containers": "包裝容器", "Personal Services": "個人服務",
    "Recreational Vehicles": "休旅車", "Residential Construction": "住宅營建",
    "Resorts & Casinos": "度假村與賭場", "Restaurants": "餐飲", "Specialty Retail": "特殊零售",
    "Textile Manufacturing": "紡織製造", "Travel Services": "旅遊服務",
    "Beverages—Brewers": "飲料—啤酒", "Beverages—Non-Alcoholic": "飲料—非酒精",
    "Beverages—Wineries & Distilleries": "飲料—酒莊與釀酒", "Confectioners": "糖果零食",
    "Discount Stores": "折扣商店", "Education & Training Services": "教育與訓練服務",
    "Farm Products": "農產品", "Food Distribution": "食品經銷", "Grocery Stores": "雜貨零售",
    "Household & Personal Products": "家用與個人用品", "Packaged Foods": "包裝食品",
    "Tobacco": "菸草", "Oil & Gas Drilling": "油氣鑽探", "Oil & Gas E&P": "油氣探採",
    "Oil & Gas Equipment & Services": "油氣設備與服務", "Oil & Gas Integrated": "油氣整合",
    "Oil & Gas Midstream": "油氣中游", "Oil & Gas Refining & Marketing": "油氣煉製與銷售",
    "Thermal Coal": "動力煤", "Uranium": "鈾礦", "Asset Management": "資產管理",
    "Banks—Diversified": "銀行—多元化", "Banks—Regional": "銀行—區域性",
    "Capital Markets": "資本市場", "Credit Services": "信用服務",
    "Financial Conglomerates": "金融控股", "Financial Data & Stock Exchanges": "金融資料與交易所",
    "Insurance Brokers": "保險經紀", "Insurance—Diversified": "保險—多元化",
    "Insurance—Life": "保險—人壽", "Insurance—Property & Casualty": "保險—產物",
    "Insurance—Reinsurance": "保險—再保", "Insurance—Specialty": "保險—特殊",
    "Mortgage Finance": "房貸金融", "Shell Companies": "空殼公司",
    "Biotechnology": "生物科技", "Diagnostics & Research": "診斷與研究",
    "Drug Manufacturers—General": "藥廠—一般",
    "Drug Manufacturers—Specialty & Generic": "藥廠—特殊與學名藥",
    "Health Information Services": "健康資訊服務", "Healthcare Plans": "健康保險計畫",
    "Medical Care Facilities": "醫療機構", "Medical Devices": "醫療器材",
    "Medical Distribution": "醫療經銷", "Medical Instruments & Supplies": "醫療儀器與用品",
    "Pharmaceutical Retailers": "藥局零售", "Aerospace & Defense": "航太與國防",
    "Airlines": "航空公司", "Airports & Air Services": "機場與航空服務",
    "Building Products & Equipment": "建築產品與設備",
    "Business Equipment & Supplies": "商用設備與用品", "Conglomerates": "綜合企業",
    "Consulting Services": "顧問服務", "Electrical Equipment & Parts": "電機設備與零件",
    "Engineering & Construction": "工程營建",
    "Farm & Heavy Construction Machinery": "農用與重型工程機械",
    "Industrial Distribution": "工業經銷", "Infrastructure Operations": "基礎建設營運",
    "Integrated Freight & Logistics": "整合貨運與物流", "Marine Shipping": "海運",
    "Metal Fabrication": "金屬加工", "Pollution & Treatment Controls": "污染防治",
    "Railroads": "鐵路", "Rental & Leasing Services": "租賃服務",
    "Security & Protection Services": "保全服務",
    "Specialty Business Services": "特殊商業服務",
    "Specialty Industrial Machinery": "特殊工業機械",
    "Staffing & Employment Services": "人力派遣", "Tools & Accessories": "工具與配件",
    "Trucking": "貨運卡車", "Waste Management": "廢棄物管理",
    "Real Estate—Development": "房地產—開發", "Real Estate—Diversified": "房地產—多元化",
    "Real Estate Services": "房地產服務", "REIT—Diversified": "REIT—多元化",
    "REIT—Healthcare Facilities": "REIT—醫療機構", "REIT—Hotel & Motel": "REIT—飯店旅館",
    "REIT—Industrial": "REIT—工業", "REIT—Mortgage": "REIT—抵押貸款",
    "REIT—Office": "REIT—辦公", "REIT—Residential": "REIT—住宅",
    "REIT—Retail": "REIT—零售", "REIT—Specialty": "REIT—特殊",
    "Communication Equipment": "通訊設備", "Computer Hardware": "電腦硬體",
    "Consumer Electronics": "消費電子", "Electronic Components": "電子零組件",
    "Information Technology Services": "資訊科技服務",
    "Scientific & Technical Instruments": "科學與技術儀器",
    "Semiconductor Equipment & Materials": "半導體設備與材料", "Semiconductors": "半導體",
    "Software—Application": "軟體—應用", "Software—Infrastructure": "軟體—基礎架構",
    "Solar": "太陽能", "Utilities—Diversified": "公用事業—多元化",
    "Utilities—Independent Power Producers": "公用事業—獨立發電",
    "Utilities—Regulated Electric": "公用事業—電力",
    "Utilities—Regulated Gas": "公用事業—燃氣",
    "Utilities—Regulated Water": "公用事業—供水",
    "Utilities—Renewable": "公用事業—再生能源",
}


def _normalize(name: str) -> str:
    return re.sub(r"\s*[-—–]\s*", "—", name)


_INDUSTRY_ZH_NORMALIZED = {_normalize(k): v for k, v in INDUSTRY_ZH.items()}


def translate_yfinance_industry(industry: str | None, sector: str | None) -> str | None:
    """把yfinance的英文industry轉成中文；查不到就退用sector，再查不到就顯示原文。"""
    if industry:
        key = _normalize(industry)
        return _INDUSTRY_ZH_NORMALIZED.get(key) or SECTOR_ZH.get(industry) or industry
    if sector:
        return SECTOR_ZH.get(sector) or sector
    return None


_twse_cache: dict[str, str] | None = None
_twse_cache_fetched_at: datetime | None = None
_tpex_cache: dict[str, str] | None = None
_tpex_cache_fetched_at: datetime | None = None


def _fetch_twse_industry_by_symbol() -> dict[str, str]:
    """回傳 {股票代號(不含.TW後綴): TWSE產業別代碼}，只涵蓋上市公司。"""
    res = requests.get(_TWSE_COMPANY_INFO_URL, timeout=15)
    res.raise_for_status()
    out = {}
    for row in res.json():
        symbol = (row.get("公司代號") or "").strip()
        code = (row.get("產業別") or "").strip()
        if symbol and code:
            out[symbol] = code
    return out


def _fetch_tpex_industry_by_symbol() -> dict[str, str]:
    """
    回傳 {股票代號(不含.TWO後綴): 產業代碼}，只涵蓋上櫃公司——TPEx(櫃買中心)
    自己的開放API，欄位是英文命名(SecuritiesCompanyCode/SecuritiesIndustryCode)，
    但驗證過產業代碼數字跟TWSE用同一套編碼(例如24=半導體業，環球晶/群聯/穩懋都對得上)，
    可以直接共用 TWSE_INDUSTRY_NAMES 對照表，不用另外維護一份。
    """
    res = requests.get(_TPEX_COMPANY_INFO_URL, timeout=15)
    res.raise_for_status()
    out = {}
    for row in res.json():
        symbol = (row.get("SecuritiesCompanyCode") or "").strip()
        code = (row.get("SecuritiesIndustryCode") or "").strip()
        if symbol and code:
            out[symbol] = code
    return out


def _get_twse_cache() -> dict[str, str]:
    global _twse_cache, _twse_cache_fetched_at
    now = datetime.now()
    if (_twse_cache is not None and _twse_cache_fetched_at
            and (now - _twse_cache_fetched_at) < timedelta(seconds=_CACHE_TTL_SECONDS)):
        return _twse_cache
    _twse_cache = _fetch_twse_industry_by_symbol()
    _twse_cache_fetched_at = now
    return _twse_cache


def _get_tpex_cache() -> dict[str, str]:
    global _tpex_cache, _tpex_cache_fetched_at
    now = datetime.now()
    if (_tpex_cache is not None and _tpex_cache_fetched_at
            and (now - _tpex_cache_fetched_at) < timedelta(seconds=_CACHE_TTL_SECONDS)):
        return _tpex_cache
    _tpex_cache = _fetch_tpex_industry_by_symbol()
    _tpex_cache_fetched_at = now
    return _tpex_cache


def classify_ticker(ticker: str) -> dict:
    """
    回傳 {"market","industry_code","industry_name","sector_name","source"}。
    台股(.TW上市/.TWO上櫃)先試對應的官方分類，查不到才退用yfinance(美股，
    或還沒被官方資料收錄的台股)。
    """
    bare_symbol = ticker.split(".")[0]
    upper = ticker.upper()
    is_twse = upper.endswith(".TW")
    is_tpex = upper.endswith(".TWO")

    if is_twse:
        try:
            twse_map = _get_twse_cache()
        except Exception:
            twse_map = {}
        code = twse_map.get(bare_symbol)
        if code:
            return {
                "market": "TW",
                "industry_code": code,
                "industry_name": TWSE_INDUSTRY_NAMES.get(code, code),
                "sector_name": "",
                "source": "twse",
            }
    elif is_tpex:
        try:
            tpex_map = _get_tpex_cache()
        except Exception:
            tpex_map = {}
        code = tpex_map.get(bare_symbol)
        if code:
            return {
                "market": "TW",
                "industry_code": code,
                "industry_name": TWSE_INDUSTRY_NAMES.get(code, code),
                "sector_name": "",
                "source": "tpex",
            }

    # 官方資料查不到(美股，或還沒被上市/上櫃基本資料收錄的新股)，退用yfinance
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        info = {}
    industry = info.get("industry")
    sector = info.get("sector")
    industry_name = translate_yfinance_industry(industry, sector)
    return {
        "market": "TW" if (is_twse or is_tpex) else "US",
        "industry_code": "",
        "industry_name": industry_name or "",
        "sector_name": SECTOR_ZH.get(sector, sector) if sector else "",
        "source": "yfinance" if industry_name else "",
    }


def list_all_tw_tickers() -> list[tuple[str, str]]:
    """
    回傳全台股市場(上市+上櫃)的 [(ticker, industry_code), ...]，直接來自TWSE/TPEx
    官方資料，不用逐檔查yfinance——用來做全市場批次灌表。
    """
    result = []
    for symbol, code in _get_twse_cache().items():
        result.append((f"{symbol}.TW", code))
    for symbol, code in _get_tpex_cache().items():
        result.append((f"{symbol}.TWO", code))
    return result


# ══════════════════════════════════════════════════════════════════════════
# 大分類彙整(2026-09-30)——TickerMap.sector 要對照這張表用，不是直接用上面
# industry_name/sector_name 那個細分類。原因：直接用細分類(台股34種+美股上百種
# industry)對TickerMap現有202檔來說會產生54種分類、其中25種只有1檔，拿來呈現
# 「各類別涵蓋準度」會太碎太難看。這裡手動把TWSE 34種官方分類 + yfinance
# 11種sector，合併收斂成18個大分類，兩邊都收斂到同一套，類別數壓在15~20種內。
# ══════════════════════════════════════════════════════════════════════════

# TWSE官方34種產業代碼 → 18個大分類(此對照表也適用TPEx，因為代碼編碼共用同一套)
TWSE_CODE_TO_BROAD: dict[str, str] = {
    '01': '傳產/原物料', '02': '消費/零售', '03': '傳產/原物料', '04': '傳產/原物料',
    '05': '工業/機械', '06': '電子/硬體', '08': '傳產/原物料', '09': '傳產/原物料',
    '10': '傳產/原物料', '11': '傳產/原物料', '12': '汽車/電動車', '14': '建材/不動產',
    '15': '航運物流', '16': '消費/零售', '17': '金融保險', '18': '消費/零售',
    '20': '其他', '21': '傳產/原物料', '22': '生技醫療', '23': '能源/公用事業',
    '24': '半導體', '25': '電子/硬體', '26': '電子/硬體', '27': '通訊服務',
    '28': '電子/硬體', '29': '電子/硬體', '30': '軟體/網路服務', '31': '電子/硬體',
    '35': '綠能環保', '36': '軟體/網路服務', '37': '消費/零售', '38': '消費/零售',
    '91': '其他',
}

# yfinance中文industry_name(細分類，翻譯後的) → 18個大分類，只收錄目前資料庫裡
# 實際出現過的值(202檔子集)；沒出現過的新值查不到才退用下面的SECTOR_TO_BROAD。
YFINANCE_INDUSTRY_TO_BROAD: dict[str, str] = {
    '半導體': '半導體', '半導體設備與材料': '半導體',
    '消費電子': '電子/硬體', '電腦硬體': '電子/硬體', '通訊設備': '電子/硬體',
    '電子零組件': '電子/硬體', '科學與技術儀器': '電子/硬體',
    '軟體—基礎架構': '軟體/網路服務', '軟體—應用': '軟體/網路服務',
    '網路內容與資訊': '軟體/網路服務', '電子遊戲與多媒體': '軟體/網路服務',
    '廣告代理': '通訊服務', '娛樂': '消費/零售', '網路零售': '消費/零售',
    '折扣商店': '消費/零售', '餐飲': '消費/零售',
    '汽車製造': '汽車/電動車',
    '航太與國防': '工業/機械', '電機設備與零件': '工業/機械',
    '農用與重型工程機械': '工業/機械', '綜合企業': '其他',
    '資本市場': '金融保險', '金融資料與交易所': '金融保險', '信用服務': '金融保險',
    '保險—多元化': '金融保險', '健康保險計畫': '金融保險',
    '藥廠—一般': '生技醫療',
    '油氣整合': '能源/公用事業', '公用事業—獨立發電': '能源/公用事業',
    '廢棄物管理': '能源/公用事業',
    '其他工業金屬與礦業': '傳產/原物料', '鋼鐵': '傳產/原物料',
}

# yfinance 11種sector(中文，翻譯後) → 18個大分類，industry_name查不到broad對照
# 時的最後備援(涵蓋未來新增、還沒出現在上面那份細分類對照表裡的標的)。
SECTOR_TO_BROAD: dict[str, str] = {
    '科技': '電子/硬體', '通訊服務': '通訊服務',
    '非必需消費': '消費/零售', '必需消費': '消費/零售',
    '能源': '能源/公用事業', '公用事業': '能源/公用事業',
    '金融服務': '金融保險', '醫療保健': '生技醫療',
    '工業': '工業/機械', '不動產': '建材/不動產', '原物料': '傳產/原物料',
}

# 非產業類的既有手動分類(指數/ETF/期貨/外匯)，本身就已經是大分類，直接沿用。
_PASSTHROUGH_BROAD = {'指數', 'ETF', '期貨', '外匯'}


def to_broad_sector(classification) -> str:
    """
    把一筆 IndustryClassification 資料換算成18個大分類之一。
    classification 需要有 industry_code/industry_name/sector_name/source
    這幾個屬性(IndustryClassification model instance 或同形狀的物件都可以)。
    """
    industry_name = (classification.industry_name or "").strip()
    if industry_name in _PASSTHROUGH_BROAD:
        return industry_name

    if classification.source in ("twse", "tpex") and classification.industry_code:
        broad = TWSE_CODE_TO_BROAD.get(classification.industry_code)
        if broad:
            return broad

    if industry_name in YFINANCE_INDUSTRY_TO_BROAD:
        return YFINANCE_INDUSTRY_TO_BROAD[industry_name]

    sector_name = (classification.sector_name or "").strip()
    if sector_name in SECTOR_TO_BROAD:
        return SECTOR_TO_BROAD[sector_name]

    return "其他"
