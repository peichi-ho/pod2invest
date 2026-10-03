# apps/assets/services/industry_zh.py
"""
yfinance `.info` 的 sector / industry 是英文（Yahoo Finance 自家分類法），
個股詳情頁要顯示中文，所以在這裡維護一份英文 → 繁體中文對照表。

沒有直接命中 industry 的話，退而用 sector 的翻譯；兩者都沒命中就直接顯示原文，
不讓頁面壞掉（yfinance 的分類法偶爾會新增沒看過的字串）。
"""
import re

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

INDUSTRY_ZH: dict[str, str] = {
    # ── Basic Materials ──────────────────────────────────────────────
    "Agricultural Inputs": "農業投入",
    "Building Materials": "建材",
    "Chemicals": "化學",
    "Specialty Chemicals": "特用化學",
    "Lumber & Wood Production": "木材",
    "Paper & Paper Products": "造紙",
    "Aluminum": "鋁",
    "Copper": "銅",
    "Other Industrial Metals & Mining": "其他工業金屬與礦業",
    "Gold": "黃金",
    "Silver": "白銀",
    "Other Precious Metals & Mining": "其他貴金屬與礦業",
    "Coking Coal": "煉焦煤",
    "Steel": "鋼鐵",

    # ── Communication Services ───────────────────────────────────────
    "Advertising Agencies": "廣告代理",
    "Publishing": "出版",
    "Broadcasting": "廣播電視",
    "Entertainment": "娛樂",
    "Internet Content & Information": "網路內容與資訊",
    "Electronic Gaming & Multimedia": "電子遊戲與多媒體",
    "Telecom Services": "電信服務",

    # ── Consumer Cyclical ─────────────────────────────────────────────
    "Apparel Manufacturing": "成衣製造",
    "Apparel Retail": "成衣零售",
    "Auto & Truck Dealerships": "汽車經銷",
    "Auto Manufacturers": "汽車製造",
    "Auto Parts": "汽車零件",
    "Department Stores": "百貨公司",
    "Footwear & Accessories": "鞋類與配件",
    "Furnishings, Fixtures & Appliances": "家具家飾與家電",
    "Gambling": "博弈",
    "Home Improvement Retail": "居家修繕零售",
    "Internet Retail": "網路零售",
    "Leisure": "休閒",
    "Lodging": "住宿",
    "Luxury Goods": "精品",
    "Packaging & Containers": "包裝容器",
    "Personal Services": "個人服務",
    "Recreational Vehicles": "休旅車",
    "Residential Construction": "住宅營建",
    "Resorts & Casinos": "度假村與賭場",
    "Restaurants": "餐飲",
    "Specialty Retail": "特殊零售",
    "Textile Manufacturing": "紡織製造",
    "Travel Services": "旅遊服務",

    # ── Consumer Defensive ────────────────────────────────────────────
    "Beverages—Brewers": "飲料—啤酒",
    "Beverages—Non-Alcoholic": "飲料—非酒精",
    "Beverages—Wineries & Distilleries": "飲料—酒莊與釀酒",
    "Confectioners": "糖果零食",
    "Discount Stores": "折扣商店",
    "Education & Training Services": "教育與訓練服務",
    "Farm Products": "農產品",
    "Food Distribution": "食品經銷",
    "Grocery Stores": "雜貨零售",
    "Household & Personal Products": "家用與個人用品",
    "Packaged Foods": "包裝食品",
    "Tobacco": "菸草",

    # ── Energy ────────────────────────────────────────────────────────
    "Oil & Gas Drilling": "油氣鑽探",
    "Oil & Gas E&P": "油氣探採",
    "Oil & Gas Equipment & Services": "油氣設備與服務",
    "Oil & Gas Integrated": "油氣整合",
    "Oil & Gas Midstream": "油氣中游",
    "Oil & Gas Refining & Marketing": "油氣煉製與銷售",
    "Thermal Coal": "動力煤",
    "Uranium": "鈾礦",

    # ── Financial Services ────────────────────────────────────────────
    "Asset Management": "資產管理",
    "Banks—Diversified": "銀行—多元化",
    "Banks—Regional": "銀行—區域性",
    "Capital Markets": "資本市場",
    "Credit Services": "信用服務",
    "Financial Conglomerates": "金融控股",
    "Financial Data & Stock Exchanges": "金融資料與交易所",
    "Insurance Brokers": "保險經紀",
    "Insurance—Diversified": "保險—多元化",
    "Insurance—Life": "保險—人壽",
    "Insurance—Property & Casualty": "保險—產物",
    "Insurance—Reinsurance": "保險—再保",
    "Insurance—Specialty": "保險—特殊",
    "Mortgage Finance": "房貸金融",
    "Shell Companies": "空殼公司",

    # ── Healthcare ────────────────────────────────────────────────────
    "Biotechnology": "生物科技",
    "Diagnostics & Research": "診斷與研究",
    "Drug Manufacturers—General": "藥廠—一般",
    "Drug Manufacturers—Specialty & Generic": "藥廠—特殊與學名藥",
    "Health Information Services": "健康資訊服務",
    "Healthcare Plans": "健康保險計畫",
    "Medical Care Facilities": "醫療機構",
    "Medical Devices": "醫療器材",
    "Medical Distribution": "醫療經銷",
    "Medical Instruments & Supplies": "醫療儀器與用品",
    "Pharmaceutical Retailers": "藥局零售",

    # ── Industrials ───────────────────────────────────────────────────
    "Aerospace & Defense": "航太與國防",
    "Airlines": "航空公司",
    "Airports & Air Services": "機場與航空服務",
    "Building Products & Equipment": "建築產品與設備",
    "Business Equipment & Supplies": "商用設備與用品",
    "Conglomerates": "綜合企業",
    "Consulting Services": "顧問服務",
    "Electrical Equipment & Parts": "電機設備與零件",
    "Engineering & Construction": "工程營建",
    "Farm & Heavy Construction Machinery": "農用與重型工程機械",
    "Industrial Distribution": "工業經銷",
    "Infrastructure Operations": "基礎建設營運",
    "Integrated Freight & Logistics": "整合貨運與物流",
    "Marine Shipping": "海運",
    "Metal Fabrication": "金屬加工",
    "Pollution & Treatment Controls": "污染防治",
    "Railroads": "鐵路",
    "Rental & Leasing Services": "租賃服務",
    "Security & Protection Services": "保全服務",
    "Specialty Business Services": "特殊商業服務",
    "Specialty Industrial Machinery": "特殊工業機械",
    "Staffing & Employment Services": "人力派遣",
    "Tools & Accessories": "工具與配件",
    "Trucking": "貨運卡車",
    "Waste Management": "廢棄物管理",

    # ── Real Estate ───────────────────────────────────────────────────
    "Real Estate—Development": "房地產—開發",
    "Real Estate—Diversified": "房地產—多元化",
    "Real Estate Services": "房地產服務",
    "REIT—Diversified": "REIT—多元化",
    "REIT—Healthcare Facilities": "REIT—醫療機構",
    "REIT—Hotel & Motel": "REIT—飯店旅館",
    "REIT—Industrial": "REIT—工業",
    "REIT—Mortgage": "REIT—抵押貸款",
    "REIT—Office": "REIT—辦公",
    "REIT—Residential": "REIT—住宅",
    "REIT—Retail": "REIT—零售",
    "REIT—Specialty": "REIT—特殊",

    # ── Technology ────────────────────────────────────────────────────
    "Communication Equipment": "通訊設備",
    "Computer Hardware": "電腦硬體",
    "Consumer Electronics": "消費電子",
    "Electronic Components": "電子零組件",
    "Information Technology Services": "資訊科技服務",
    "Scientific & Technical Instruments": "科學與技術儀器",
    "Semiconductor Equipment & Materials": "半導體設備與材料",
    "Semiconductors": "半導體",
    "Software—Application": "軟體—應用",
    "Software—Infrastructure": "軟體—基礎架構",
    "Solar": "太陽能",

    # ── Utilities ─────────────────────────────────────────────────────
    "Utilities—Diversified": "公用事業—多元化",
    "Utilities—Independent Power Producers": "公用事業—獨立發電",
    "Utilities—Regulated Electric": "公用事業—電力",
    "Utilities—Regulated Gas": "公用事業—燃氣",
    "Utilities—Regulated Water": "公用事業—供水",
    "Utilities—Renewable": "公用事業—再生能源",
}


def _normalize(name: str) -> str:
    """yfinance 對複合產業名稱的分隔符不太一致（曾見過 " - " 和 "—" 兩種），統一成 "—" 再查表。"""
    return re.sub(r"\s*[-—–]\s*", "—", name)


_INDUSTRY_ZH_NORMALIZED = {_normalize(k): v for k, v in INDUSTRY_ZH.items()}


def translate_industry(industry: str | None, sector: str | None) -> str | None:
    """把 yfinance 的英文 industry 轉成中文；查不到就退用 sector，再查不到就顯示原文。"""
    if industry:
        key = _normalize(industry)
        return _INDUSTRY_ZH_NORMALIZED.get(key) or SECTOR_ZH.get(industry) or industry
    if sector:
        return SECTOR_ZH.get(sector) or sector
    return None
