import calendar
import html as _html_mod
import json
import math
import xml.etree.ElementTree as ET
import re
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

import yfinance as yf
import requests
from bs4 import BeautifulSoup
from rest_framework.views import APIView
from rest_framework.response import Response

try:
    from deep_translator import GoogleTranslator
    _translator = GoogleTranslator(source='auto', target='zh-TW')

    def _translate(text: str) -> str:
        if not text:
            return text
        if re.search(r'[一-鿿]', text):
            return text
        try:
            return _translator.translate(text[:500]) or text
        except Exception:
            return text

    def _translate_content(text: str) -> str:
        """Translate article body paragraph by paragraph (max 8 paragraphs)."""
        if not text:
            return text
        zh_ratio = len(re.findall(r'[一-鿿]', text)) / max(len(text), 1)
        if zh_ratio > 0.15:
            return text
        paragraphs = [p for p in text.split('\n\n') if p.strip()]
        result = []
        for para in paragraphs[:8]:
            try:
                t = _translator.translate(para[:800]) or para
                result.append(t)
            except Exception:
                result.append(para)
        return '\n\n'.join(result)

except ImportError:
    def _translate(text: str) -> str:
        return text

    def _translate_content(text: str) -> str:
        return text


# ── Alias table: 常見股票的別稱對應，優先於 substring 比對 ──────────────────
# key: 小寫別稱, value: (ticker, canonical_name, zh_name)
# zh_name 用於鉅亨網查詢，美股也能搜到中文新聞
_ALIAS_MAP = {
    # ── 美股：科技大廠 ────────────────────────────────────────────────────
    # 蘋果
    'aapl': ('AAPL', 'Apple Inc', '蘋果'), 'apple inc': ('AAPL', 'Apple Inc', '蘋果'),
    'apple': ('AAPL', 'Apple Inc', '蘋果'), '蘋果公司': ('AAPL', 'Apple Inc', '蘋果'), '蘋果': ('AAPL', 'Apple Inc', '蘋果'),
    # 輝達 / NVIDIA
    '輝達': ('NVDA', 'NVIDIA', '輝達'), 'nvidia': ('NVDA', 'NVIDIA', '輝達'), 'nvda': ('NVDA', 'NVIDIA', '輝達'),
    # 微軟
    'msft': ('MSFT', 'Microsoft', '微軟'), 'microsoft': ('MSFT', 'Microsoft', '微軟'), '微軟': ('MSFT', 'Microsoft', '微軟'),
    # Google / Alphabet
    'googl': ('GOOGL', 'Alphabet', 'Google'), 'goog': ('GOOGL', 'Alphabet', 'Google'),
    'google': ('GOOGL', 'Alphabet', 'Google'), 'alphabet': ('GOOGL', 'Alphabet', 'Google'),
    '谷歌': ('GOOGL', 'Alphabet', 'Google'),
    # Meta / Facebook
    'meta': ('META', 'Meta', 'Meta'), 'facebook': ('META', 'Meta', 'Meta'), '臉書': ('META', 'Meta', 'Meta'),
    # 亞馬遜
    'amzn': ('AMZN', 'Amazon', '亞馬遜'), 'amazon': ('AMZN', 'Amazon', '亞馬遜'), '亞馬遜': ('AMZN', 'Amazon', '亞馬遜'),
    # 特斯拉
    'tsla': ('TSLA', 'Tesla', '特斯拉'), 'tesla': ('TSLA', 'Tesla', '特斯拉'), '特斯拉': ('TSLA', 'Tesla', '特斯拉'),
    # Netflix
    'nflx': ('NFLX', 'Netflix', '奈飛'), 'netflix': ('NFLX', 'Netflix', '奈飛'), '奈飛': ('NFLX', 'Netflix', '奈飛'),
    # AMD / 超微
    'amd': ('AMD', 'AMD', '超微'), '超微': ('AMD', 'AMD', '超微'),
    # Intel / 英特爾
    'intc': ('INTC', 'Intel', '英特爾'), 'intel': ('INTC', 'Intel', '英特爾'), '英特爾': ('INTC', 'Intel', '英特爾'),
    # Qualcomm / 高通
    'qcom': ('QCOM', 'Qualcomm', '高通'), 'qualcomm': ('QCOM', 'Qualcomm', '高通'), '高通': ('QCOM', 'Qualcomm', '高通'),
    # Broadcom / 博通
    'avgo': ('AVGO', 'Broadcom', '博通'), 'broadcom': ('AVGO', 'Broadcom', '博通'), '博通': ('AVGO', 'Broadcom', '博通'),
    # ARM
    'arm': ('ARM', 'ARM Holdings', 'ARM'),
    # Palantir
    'pltr': ('PLTR', 'Palantir', 'Palantir'), 'palantir': ('PLTR', 'Palantir', 'Palantir'),

    # ── 美股：金融 ────────────────────────────────────────────────────────
    # 波克夏
    'brk.b': ('BRK-B', 'Berkshire Hathaway', '波克夏'), 'brkb': ('BRK-B', 'Berkshire Hathaway', '波克夏'),
    'berkshire': ('BRK-B', 'Berkshire Hathaway', '波克夏'), '波克夏': ('BRK-B', 'Berkshire Hathaway', '波克夏'),
    # 摩根大通
    'jpm': ('JPM', 'JPMorgan Chase', '摩根大通'), 'jpmorgan': ('JPM', 'JPMorgan Chase', '摩根大通'), '摩根大通': ('JPM', 'JPMorgan Chase', '摩根大通'),
    # 高盛
    'gs': ('GS', 'Goldman Sachs', '高盛'), 'goldman sachs': ('GS', 'Goldman Sachs', '高盛'), '高盛': ('GS', 'Goldman Sachs', '高盛'),
    # 輝立資本 / Visa
    'v': ('V', 'Visa', 'Visa'), 'visa': ('V', 'Visa', 'Visa'),

    # ── 美股：電動車 / 能源 ───────────────────────────────────────────────
    # 超級微型電腦 / Super Micro
    'smci': ('SMCI', 'Super Micro Computer', 'Super Micro'), 'supermicro': ('SMCI', 'Super Micro Computer', 'Super Micro'),
    # Exxon
    'xom': ('XOM', 'ExxonMobil', '埃克森'), 'exxon': ('XOM', 'ExxonMobil', '埃克森'), '埃克森': ('XOM', 'ExxonMobil', '埃克森'),

    # ── 美股：ETF ─────────────────────────────────────────────────────────
    'spy': ('SPY', 'SPDR S&P 500 ETF', 'SPY'), 's&p500': ('SPY', 'SPDR S&P 500 ETF', 'SPY'),
    'qqq': ('QQQ', 'Invesco QQQ', 'QQQ'), 'nasdaq etf': ('QQQ', 'Invesco QQQ', 'QQQ'),
    'voo': ('VOO', 'Vanguard S&P 500 ETF', 'VOO'),
    'soxx': ('SOXX', 'iShares Semiconductor ETF', 'SOXX'), '費城半導體': ('SOXX', 'iShares Semiconductor ETF', 'SOXX'),

    # ── 台股：權值股 ──────────────────────────────────────────────────────
    # 台積電
    '台積電': ('2330.TW', '台積電', '台積電'), 'tsmc': ('2330.TW', '台積電', '台積電'), '2330': ('2330.TW', '台積電', '台積電'),
    # 鴻海
    '鴻海': ('2317.TW', '鴻海', '鴻海'), 'foxconn': ('2317.TW', '鴻海', '鴻海'), '2317': ('2317.TW', '鴻海', '鴻海'),
    # 聯發科
    '聯發科': ('2454.TW', '聯發科', '聯發科'), 'mediatek': ('2454.TW', '聯發科', '聯發科'), '2454': ('2454.TW', '聯發科', '聯發科'),
    # 聯電
    '聯電': ('2303.TW', '聯電', '聯電'), 'umc': ('2303.TW', '聯電', '聯電'), '2303': ('2303.TW', '聯電', '聯電'),
    # 廣達
    '廣達': ('2382.TW', '廣達', '廣達'), '2382': ('2382.TW', '廣達', '廣達'),
    # 緯創
    '緯創': ('3231.TW', '緯創', '緯創'), '3231': ('3231.TW', '緯創', '緯創'),
    # 緯穎
    '緯穎': ('6669.TW', '緯穎', '緯穎'), '6669': ('6669.TW', '緯穎', '緯穎'),
    # 英業達
    '英業達': ('2356.TW', '英業達', '英業達'), '2356': ('2356.TW', '英業達', '英業達'),
    # 仁寶
    '仁寶': ('2324.TW', '仁寶', '仁寶'), '2324': ('2324.TW', '仁寶', '仁寶'),
    # 瑞昱
    '瑞昱': ('2379.TW', '瑞昱', '瑞昱'), 'realtek': ('2379.TW', '瑞昱', '瑞昱'), '2379': ('2379.TW', '瑞昱', '瑞昱'),
    # 日月光
    '日月光': ('3711.TW', '日月光投控', '日月光'), 'ase': ('3711.TW', '日月光投控', '日月光'), '3711': ('3711.TW', '日月光投控', '日月光'),
    # 矽品 / 京元電
    '矽品': ('2325.TW', '矽品', '矽品'), '2325': ('2325.TW', '矽品', '矽品'),
    # 台達電
    '台達電': ('2308.TW', '台達電', '台達電'), 'delta': ('2308.TW', '台達電', '台達電'), '2308': ('2308.TW', '台達電', '台達電'),
    # 研華
    '研華': ('2395.TW', '研華', '研華'), 'advantech': ('2395.TW', '研華', '研華'), '2395': ('2395.TW', '研華', '研華'),
    # 中華電
    '中華電': ('2412.TW', '中華電信', '中華電信'), '中華電信': ('2412.TW', '中華電信', '中華電信'), '2412': ('2412.TW', '中華電信', '中華電信'),
    # 台灣大
    '台灣大': ('3045.TW', '台灣大哥大', '台灣大哥大'), '台灣大哥大': ('3045.TW', '台灣大哥大', '台灣大哥大'), '3045': ('3045.TW', '台灣大哥大', '台灣大哥大'),
    # 遠傳
    '遠傳': ('4904.TW', '遠傳電信', '遠傳'), '4904': ('4904.TW', '遠傳電信', '遠傳'),
    # 玉山金
    '玉山金': ('2884.TW', '玉山金控', '玉山金'), '2884': ('2884.TW', '玉山金控', '玉山金'),
    # 國泰金
    '國泰金': ('2882.TW', '國泰金控', '國泰金'), '2882': ('2882.TW', '國泰金控', '國泰金'),
    # 富邦金
    '富邦金': ('2881.TW', '富邦金控', '富邦金'), '2881': ('2881.TW', '富邦金控', '富邦金'),
    # 中信金
    '中信金': ('2891.TW', '中國信託金控', '中信金'), '2891': ('2891.TW', '中國信託金控', '中信金'),
    # 永豐金
    '永豐金': ('2890.TW', '永豐金控', '永豐金'), '2890': ('2890.TW', '永豐金控', '永豐金'),

    # ── 台股：IC 設計 / 半導體 ──────────────────────────────────────────────
    # 創意電子
    '創意': ('3443.TW', '創意電子', '創意電子'), '創意電子': ('3443.TW', '創意電子', '創意電子'), '3443': ('3443.TW', '創意電子', '創意電子'),
    # 矽力-KY
    '矽力': ('6415.TW', '矽力-KY', '矽力'), '6415': ('6415.TW', '矽力-KY', '矽力'),
    # 祥碩
    '祥碩': ('5269.TW', '祥碩', '祥碩'), '5269': ('5269.TW', '祥碩', '祥碩'),
    # M31
    'm31': ('6643.TW', 'M31', 'M31'), '6643': ('6643.TW', 'M31', 'M31'),
    # 世芯-KY
    '世芯': ('3661.TW', '世芯-KY', '世芯'), '3661': ('3661.TW', '世芯-KY', '世芯'),
    # 金像電
    '金像電': ('2368.TW', '金像電', '金像電'), '2368': ('2368.TW', '金像電', '金像電'),
    # 譜瑞-KY
    '譜瑞': ('4966.TW', '譜瑞-KY', '譜瑞'), '4966': ('4966.TW', '譜瑞-KY', '譜瑞'),
    # 力旺
    '力旺': ('3529.TW', '力旺', '力旺'), '3529': ('3529.TW', '力旺', '力旺'),
    # 智原
    '智原': ('3035.TW', '智原', '智原'), '3035': ('3035.TW', '智原', '智原'),
    # 台灣高鐵
    '高鐵': ('2633.TW', '台灣高速鐵路', '台灣高鐵'), '台灣高鐵': ('2633.TW', '台灣高速鐵路', '台灣高鐵'), '2633': ('2633.TW', '台灣高速鐵路', '台灣高鐵'),
    # 台塑四寶
    '台塑': ('1301.TW', '台塑', '台塑'), '1301': ('1301.TW', '台塑', '台塑'),
    '南亞': ('1303.TW', '南亞', '南亞'), '1303': ('1303.TW', '南亞', '南亞'),
    '台化': ('1326.TW', '台化', '台化'), '1326': ('1326.TW', '台化', '台化'),
    '台塑化': ('6505.TW', '台塑化', '台塑化'), '6505': ('6505.TW', '台塑化', '台塑化'),
    # 其他常見個股
    '大立光': ('3008.TW', '大立光', '大立光'), '3008': ('3008.TW', '大立光', '大立光'),
    '可成': ('2474.TW', '可成', '可成'), '2474': ('2474.TW', '可成', '可成'),
    '和碩': ('4938.TW', '和碩', '和碩'), '4938': ('4938.TW', '和碩', '和碩'),
    '台光電': ('2383.TW', '台光電', '台光電'), '2383': ('2383.TW', '台光電', '台光電'),
    '欣興': ('3037.TW', '欣興', '欣興'), '3037': ('3037.TW', '欣興', '欣興'),
    '華碩': ('2357.TW', '華碩', '華碩'), '2357': ('2357.TW', '華碩', '華碩'),
    '宏碁': ('2353.TW', '宏碁', '宏碁'), '2353': ('2353.TW', '宏碁', '宏碁'),
    '奇鋐': ('3017.TW', '奇鋐', '奇鋐'), '3017': ('3017.TW', '奇鋐', '奇鋐'),
    '健策': ('3653.TW', '健策', '健策'), '3653': ('3653.TW', '健策', '健策'),
    '超豐': ('2441.TW', '超豐', '超豐'), '2441': ('2441.TW', '超豐', '超豐'),
    '京元電': ('2449.TW', '京元電子', '京元電'), '京元電子': ('2449.TW', '京元電子', '京元電'), '2449': ('2449.TW', '京元電子', '京元電'),

    # ── 台股：ETF ─────────────────────────────────────────────────────────
    '0050': ('0050.TW', '元大台灣50', '元大台灣50'), '元大台灣50': ('0050.TW', '元大台灣50', '元大台灣50'),
    '0056': ('0056.TW', '元大高股息', '元大高股息'), '元大高股息': ('0056.TW', '元大高股息', '元大高股息'),
    '00878': ('00878.TW', '國泰永續高股息', '國泰永續高股息'), '國泰永續高股息': ('00878.TW', '國泰永續高股息', '國泰永續高股息'),
    '00929': ('00929.TW', '復華台灣科技優息', '復華台灣科技優息'), '復華台灣科技優息': ('00929.TW', '復華台灣科技優息', '復華台灣科技優息'),
    '00919': ('00919.TW', '群益台灣精選高息', '群益台灣精選高息'), '群益台灣精選高息': ('00919.TW', '群益台灣精選高息', '群益台灣精選高息'),
    '00940': ('00940.TW', '元大台灣價值高息', '元大台灣價值高息'), '元大台灣價值高息': ('00940.TW', '元大台灣價值高息', '元大台灣價值高息'),

    # ── 台股指數 ──────────────────────────────────────────────────────────
    # zh_name 用「加權指數」，鉅亨網文章標題常出現這個詞
    '^twii': ('^TWII', '台股加權指數', '加權指數'),
    'twii':  ('^TWII', '台股加權指數', '加權指數'),
    '加權指數': ('^TWII', '台股加權指數', '加權指數'),
    '台股加權': ('^TWII', '台股加權指數', '加權指數'),
    '大盤':    ('^TWII', '台股加權指數', '加權指數'),
}

# 這些詞單獨出現時不要自動解析成股票，要求更明確的輸入
_AMBIGUOUS_TERMS = {'中華', '台灣', '聯合', '統一', '中信', '國泰', '富邦', '永豐'}

_BLOCKED_DOMAINS = [
    'tradingview.com',
    'investing.com',
]

_BLOCKED_TITLE_KEYWORDS = [
    '持倉清單', '持股清單', '成分股清單', '技術分析', '技術指標',
    'ETF基本資料', '基本資料頁', 'holdings', 'technical analysis',
    'stock screener',
]

_UNSUPPORTED_DOMAINS = [
    'tradingview.com',
    'investing.com',
    'news.google.com',
]

# 財經語境詞
_FINANCE_TERMS_ZH = ['股價', '營收', '財報', '法說', '外資', '投信', '自營', '漲停', '跌停',
                      '季報', '年報', '獲利', '配息', '除權', '除息', '融資', '融券', '庫藏股']
_FINANCE_TERMS_EN = ['stock', 'earnings', 'revenue', 'shares', 'analyst', 'nasdaq', 'nyse',
                      'dividend', 'forecast', 'guidance', 'quarterly', 'eps', 'buyback', 'sec']

# 公司事件詞：這類詞出現在標題才算跟公司營運直接相關
_COMPANY_EVENT_ZH = ['新產品', '發布', '供應鏈', '併購', '裁員', '擴廠', '投資', '合作',
                      '訴訟', '監管', '反壟斷', '股東會', '董事會', '執行長', '執行董事',
                      '市值', '股票', '上市', '下市', '回購']
_COMPANY_EVENT_EN = ['product', 'launch', 'supply chain', 'acquisition', 'merger', 'layoff',
                      'investment', 'partnership', 'lawsuit', 'regulation', 'antitrust',
                      'ceo', 'cfo', 'executive', 'market cap', 'ipo', 'buyback', 'recall']

# 廣告/促銷/免責聲明偵測：含這些詞代表文章是廣告或法規公告，不是新聞
_PROMO_TERMS = [
    '鉅亨買基金', '申購前應詳閱', '本資料僅供參考', '不保證最低之收益',
    '基金公開說明書', '投資人須知', '風險報酬等級', '獨立經營管理',
    '客服信箱', '服務專線', '申購前應自行了解',
]

# 排除關鍵詞：主題明顯是政治/外交/社會/娛樂，不是公司投資新聞
_OFFtopic_TERMS = ['外交', '峰會', '高層會談', '軍事', '選舉', '總統', '國防', '制裁',
                   '娛樂', '藝人', '電影', '音樂', '旅遊', '食譜', '料理', '水果', '農業',
                   'diplomatic', 'summit', 'military', 'election', 'president', 'sanction',
                   'entertainment', 'celebrity', 'travel', 'recipe', 'fruit']

_FINANCE_SOURCES = ['鉅亨網', '經濟日報', '工商時報', '聯合財經網', 'MoneyDJ', 'Reuters', 'Bloomberg',
                    'CNBC', 'MarketWatch', 'Barron', 'Seeking Alpha', '財訊', '商業周刊']


def _is_non_news(url: str, title: str) -> bool:
    url_lower = url.lower()
    for domain in _BLOCKED_DOMAINS:
        if domain in url_lower:
            return True
    title_lower = title.lower()
    for kw in _BLOCKED_TITLE_KEYWORDS:
        if kw.lower() in title_lower:
            return True
    return False


def _is_chinese(text: str) -> bool:
    """Return True if text contains at least one Chinese character."""
    return bool(re.search(r'[一-鿿]', text or ''))


def _make_keywords(ticker: str, name: str) -> dict:
    """回傳 { ticker, code, name, zh_name, finance_terms } 供 scoring 使用。

    zh_name 查詢順序（對台股、美股、指數一致）：
      1. ticker_map DB（按 ticker 欄精確查）
      2. _ALIAS_MAP 反查（本地硬編碼備援）
      3. name（若已是中文直接用）
      4. code（純數字/代碼，最後保底）
    """
    code = re.sub(r'\.[A-Z]+$', '', ticker)
    is_tw = ticker.endswith('.TW') or ticker.endswith('.TWO')
    zh_name = name  # 預設值，後面嘗試覆蓋成更準確的中文名

    # Step 1：ticker_map DB（最權威來源，台股/美股/指數都有）
    try:
        from apps.summaries.models import TickerMap
        tm = TickerMap.objects.using("summariesdb").filter(ticker=ticker).first()
        if tm and tm.zh_name:
            zh_name = tm.zh_name
    except Exception:
        pass

    # Step 2：_ALIAS_MAP 反查（ticker_map 沒收錄時）
    if not _is_chinese(zh_name) or zh_name == name:
        for val in _ALIAS_MAP.values():
            if val[0] == ticker and len(val) >= 3 and val[2]:
                zh_name = val[2]
                break

    # Step 3：name 本身若是中文就直接用（TWSE 回傳的台股名稱）
    if not _is_chinese(zh_name) and _is_chinese(name):
        zh_name = name

    # Step 4：最後保底用 code
    if not _is_chinese(zh_name):
        zh_name = code
    return {
        'ticker': ticker,
        'code': code,
        'name': name,
        'zh_name': zh_name,
        'finance_terms': _FINANCE_TERMS_ZH + _FINANCE_TERMS_EN if not is_tw else _FINANCE_TERMS_ZH,
        'is_tw': is_tw,
    }


def _score_article(title: str, snippet: str, source: str, kw: dict) -> tuple[int, list[str]]:
    """
    股票投資相關新聞篩選，回傳 (score, reasons)。
    通過門檻：score >= 3。

    核心規則：
    - 標題提到公司/ticker 是強訊號，內文偶爾提到不算
    - 必須同時有財經或公司事件詞，才算投資相關新聞
    - 主題明顯是政治/外交/娛樂，直接拒絕
    """
    score = 0
    reasons = []
    title_lower = title.lower()
    snippet_lower = snippet.lower()

    # ── 廣告/促銷/免責聲明：直接拒絕 ────────────────────────────────────
    promo_hit = next((t for t in _PROMO_TERMS if t in title or t in snippet), None)
    if promo_hit:
        return 0, [f'rejected: promotional/disclaimer content ({promo_hit})']

    # 只看前 300 字，避免內文深處偶爾提到就加分
    early_body = snippet_lower[:300]
    all_text = title_lower + ' ' + snippet_lower

    code = kw['code'].lower()
    name = (kw['name'] or '').lower()
    zh_name = kw.get('zh_name', '') or ''

    # ── 主題排除：政治/外交/娛樂新聞，直接拒絕 ──────────────────────────
    if any(t in all_text for t in _OFFtopic_TERMS):
        # 只有標題同時明確提到 ticker 或公司名才放行（例如真的是公司被制裁的新聞）
        title_has_company = (code in title_lower or name in title_lower or
                             (zh_name and zh_name in title_lower))
        if not title_has_company:
            return 0, ['rejected: off-topic (political/entertainment/lifestyle)']

    # ── 標題命中（強訊號）────────────────────────────────────────────────
    title_has_ticker = code in title_lower
    title_has_name = (name and name in title_lower) or (zh_name and zh_name in title_lower)

    if title_has_ticker:
        score += 4
        reasons.append(f"title has ticker {kw['code']}")
    if title_has_name:
        score += 3
        reasons.append(f"title has company name")

    # ── 財經詞命中（標題 > 前300字 > 全文）──────────────────────────────
    finance_all = kw['finance_terms']
    title_finance = [t for t in finance_all if t in title_lower]
    early_finance = [t for t in finance_all if t in early_body]

    if title_finance:
        score += 3
        reasons.append(f"title has finance term: {title_finance[0]}")
    elif early_finance:
        score += 2
        reasons.append(f"early body has finance term: {early_finance[0]}")

    # ── 公司事件詞（供應鏈、財報、訴訟等）──────────────────────────────
    event_terms = _COMPANY_EVENT_ZH + _COMPANY_EVENT_EN
    title_events = [t for t in event_terms if t in title_lower]
    early_events = [t for t in event_terms if t in early_body]

    if title_events:
        score += 2
        reasons.append(f"title has company event: {title_events[0]}")
    elif early_events:
        score += 1
        reasons.append(f"early body has company event: {early_events[0]}")

    # ── 標題完全沒提到公司：內文才提到，大幅扣分 ─────────────────────────
    if not title_has_ticker and not title_has_name:
        score -= 3
        reasons.append('penalty: company not in title')

    # ── 財經媒體來源小加分 ────────────────────────────────────────────────
    if any(s.lower() in source.lower() for s in _FINANCE_SOURCES):
        score += 1
        reasons.append(f"source {source}")

    return score, reasons


_tw_stock_cache = {}
_tw_stock_cache_time = None


def _get_tw_stock_map():
    """Return {code: name} for all TWSE listed stocks. Cached for 24h."""
    global _tw_stock_cache, _tw_stock_cache_time
    now = datetime.now()
    if _tw_stock_cache and _tw_stock_cache_time and (now - _tw_stock_cache_time) < timedelta(hours=24):
        return _tw_stock_cache
    try:
        res = requests.get(
            'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
            timeout=10,
            headers={'Accept': 'application/json'},
        )
        data = res.json()
        _tw_stock_cache = {item['Code']: item['Name'] for item in data if 'Code' in item and 'Name' in item}
        _tw_stock_cache_time = now
    except Exception:
        pass
    return _tw_stock_cache


def _resolve_ticker(query: str) -> str | dict:
    """
    把使用者輸入轉成 yfinance ticker。
    若無法明確判斷（多個候選），回傳 {'candidates': [...]} 讓前端處理。
    """
    query = query.strip()
    query_lower = query.lower()

    # 1. 模糊詞：直接拒絕，要求更明確
    if query in _AMBIGUOUS_TERMS:
        return {'error': f'「{query}」可能對應多家公司，請輸入更完整的公司名稱或股票代號'}

    # 2. ticker_map DB 精確查詢（asset_name 完全符合）
    try:
        from apps.summaries.models import TickerMap
        tm = TickerMap.objects.using("summariesdb").filter(asset_name=query).first()
        if tm:
            return tm.ticker
    except Exception:
        pass

    # 3. Alias map fallback（涵蓋 ticker_map 沒收錄的別稱）
    if query_lower in _ALIAS_MAP:
        ticker, *_ = _ALIAS_MAP[query_lower]
        return ticker

    # 3. 已有點號（使用者直接輸入 2330.TW 等）
    if '.' in query:
        return query

    # 4. 純數字 → 台股
    if query.isdigit():
        return query + '.TW'

    # 5. 全大寫英文 → 直接當美股 ticker
    if query.isalpha() and query.isupper() and query.isascii():
        return query

    # 6. TWSE 清單 substring 比對 → 若多個命中，回傳候選
    stock_map = _get_tw_stock_map()
    query_lower_str = query.lower()
    matches = [
        (code, name) for code, name in stock_map.items()
        if query_lower_str in name.lower() or name.lower() in query_lower_str
    ]
    if len(matches) == 1:
        return matches[0][0] + '.TW'
    if len(matches) > 1:
        candidates = [{'ticker': c + '.TW', 'name': n} for c, n in matches[:5]]
        return {'candidates': candidates, 'query': query}

    # 7. TickerMap 資料庫
    try:
        from apps.summaries.models import TickerMap
        match = TickerMap.objects.using("summariesdb").filter(
            asset_name__icontains=query
        ).first()
        if match:
            return match.ticker
    except Exception:
        pass

    # 8. yfinance Search fallback
    try:
        results = yf.Search(query, max_results=10).quotes
        for r in results:
            sym = r.get('symbol', '')
            if sym.endswith('.TW'):
                return sym
        for r in results:
            sym = r.get('symbol', '')
            if sym and '.' not in sym:
                return sym
    except Exception:
        pass

    return query + '.TW'


def _get_display_name(ticker: str) -> str:
    code = ticker.replace('.TW', '')
    tw_map = _get_tw_stock_map()
    if code in tw_map:
        return tw_map[code]
    try:
        info = yf.Ticker(ticker).info
        return info.get('longName') or info.get('shortName') or ticker
    except Exception:
        return ticker


def _parse_pub_date(pub_str: str):
    try:
        return parsedate_to_datetime(pub_str)
    except Exception:
        return None


def _extract_snippet(html: str) -> str:
    if not html:
        return ''
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:300]


def _fetch_google_news(query: str, kw: dict, limit: int = 5) -> list:
    """Google News RSS，用 scoring 過濾。"""
    url = f'https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant'
    try:
        r = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        root = ET.fromstring(r.content)
        raw_items = root.findall('.//item')[:30]

        now = datetime.now(timezone.utc)
        candidates = []
        for item in raw_items:
            title = (item.findtext('title') or '').strip()
            link = (item.findtext('link') or '').strip()
            pub = (item.findtext('pubDate') or '').strip()
            source_el = item.find('source')
            source = (source_el.text or '').strip() if source_el is not None else ''

            if not title or not link:
                continue
            if _is_non_news(link, title):
                continue

            score, reasons = _score_article(title, '', source, kw)
            if score < 3:
                continue

            candidates.append({
                'title': title, 'url': link, 'pub': pub,
                'provider': source, 'snippet': '',
                'relevance_reason': ', '.join(reasons),
                '_dt': _parse_pub_date(pub),
                '_score': score,
            })

        # 先取 30 天，不足再擴到 90 天
        result = candidates
        for days in (30, 90):
            cutoff = timedelta(days=days)
            recent = [c for c in candidates if c['_dt'] and (now - c['_dt']) <= cutoff]
            if len(recent) >= limit:
                result = recent
                break

        result.sort(key=lambda x: x['_dt'] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        out = []
        for c in result[:limit]:
            c.pop('_dt', None)
            c.pop('_score', None)
            out.append(c)
        return out
    except Exception:
        return []


def _fetch_yahoo_news(ticker: str, kw: dict, limit: int = 5) -> list:
    """Yahoo Finance RSS fallback，用 scoring 過濾。"""
    url = f'https://finance.yahoo.com/rss/headline?s={requests.utils.quote(ticker)}'
    now = datetime.now(timezone.utc)
    try:
        r = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        root = ET.fromstring(r.content)
        items = root.findall('.//item')
        news = []
        for item in items:
            if len(news) >= limit:
                break
            title = (item.findtext('title') or '').strip()
            link = (item.findtext('link') or '').strip()
            pub = (item.findtext('pubDate') or '').strip()
            desc_el = item.find('description')
            snippet = _extract_snippet(desc_el.text if desc_el is not None else '')
            source_el = item.find('{https://search.yahoo.com/mrss/}credit')
            source = (source_el.text or '').strip() if source_el is not None else ''
            if not title or not link:
                continue
            if _is_non_news(link, title):
                continue

            score, reasons = _score_article(title, snippet, source, kw)
            if score < 3:
                continue

            pub_dt = _parse_pub_date(pub)
            if pub_dt and (now - pub_dt) > timedelta(days=90):
                continue

            news.append({
                'title': _translate(title), 'url': link, 'pub': pub,
                'provider': source, 'snippet': snippet,
                'relevance_reason': ', '.join(reasons),
            })
        return news
    except Exception:
        return []


def _strip_html_to_text(raw: str) -> str:
    """
    把 HTML（含二次編碼的 &lt;p&gt; 形式）轉成帶段落換行的純文字。
    1. unescape 一次（&lt; → <）
    2. 若還有殘留 entity，再 unescape 一次（處理雙重編碼）
    3. </p> / <br> → 換行，其他 tag → 移除
    """
    s = _html_mod.unescape(raw or '')
    # 如果還有 &lt; 表示雙重編碼，再解一次
    if '&lt;' in s or '&amp;' in s:
        s = _html_mod.unescape(s)
    s = re.sub(r'</p\s*>', '\n\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<br\s*/?>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<[^>]+>', '', s)
    return s


def _anue_item_to_news(item: dict) -> dict:
    """Convert a raw Anue newslist item to our news dict."""
    news_id = item.get('newsId')
    title = re.sub(r'<[^>]+>', '', _html_mod.unescape(item.get('title') or '')).strip()
    content_html = item.get('content') or ''
    # 保留段落結構（支援二次編碼）
    s = _strip_html_to_text(content_html)
    # 清理每行首尾空白，壓縮連續空白行
    lines = [ln.strip() for ln in s.splitlines()]
    cleaned_lines: list[str] = []
    blank = 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                cleaned_lines.append('')
        else:
            blank = 0
            cleaned_lines.append(ln)
    snippet = '\n'.join(cleaned_lines).strip()[:2000]
    if len(re.findall(r'[一-鿿]', snippet)) < 5:
        snippet = ''
    publish_at = item.get('publishAt')
    pub_dt = datetime.fromtimestamp(publish_at, tz=timezone.utc) if publish_at else None
    pub_str = pub_dt.strftime('%a, %d %b %Y %H:%M:%S GMT') if pub_dt else ''
    return {
        'title': title,
        'url': f'https://news.cnyes.com/news/id/{news_id}',
        'pub': pub_str,
        'provider': '鉅亨網',
        'snippet': snippet,
        '_dt': pub_dt,
    }


_ANUE_NEWSLIST_HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Accept': 'application/json',
    'Referer': 'https://news.cnyes.com/',
}


def _is_tw_etf(code: str) -> bool:
    """台灣 ETF 代碼判斷：00 開頭，4–6 位數字。如 0050, 0056, 006208, 00878。"""
    return bool(re.match(r'^00\d{2,4}$', code))


def _fetch_anue_newslist(category: str, page: int) -> list:
    """單頁抓取鉅亨 newslist，回傳原始 item list；失敗回空 list。"""
    try:
        url = (f'https://api.cnyes.com/media/api/v1/newslist/category/{category}'
               f'?limit=30&page={page}')
        r = requests.get(url, timeout=10, headers=_ANUE_NEWSLIST_HEADERS)
        if r.status_code != 200:
            return []
        return r.json().get('items', {}).get('data', []) or []
    except Exception:
        return []


def _fetch_anue_news_by_stock_code(code: str, limit: int = 5, max_pages: int = 6,
                                   zh_name: str = '') -> list:
    """
    Anue newslist API：多策略抓取。

    第一輪：tw_stock（+ tw_etf if ETF）的 stock 欄位精準比對。
    第二輪：若不足 limit，同樣的已抓頁面改用 title+content 關鍵字補足。
    第三輪：若仍不足，額外多翻頁（max_pages*2）再做關鍵字搜尋。

    台股：code='2330' / ETF code='0050'
    美股：code='NVDA'（也搜 'US-NVDA'）
    指數 ^TWII：無 stock 欄位，回空由上層處理。
    """
    if not code or code.startswith('^'):
        return []

    search_codes = {code, f'US-{code}'}
    now_dt = datetime.now(timezone.utc)
    is_etf = _is_tw_etf(code)

    # ── 決定要抓的分類 ────────────────────────────────────────────────────────
    categories = ['tw_stock']
    if is_etf:
        categories.append('tw_etf')   # ETF 追加 tw_etf 分類

    # ── 共用頁面快取：{category: [page1_items, page2_items, ...]} ─────────────
    page_cache: dict[str, list[list]] = {cat: [] for cat in categories}

    def _ensure_pages(cat: str, max_p: int) -> None:
        cached = page_cache[cat]
        for page in range(len(cached) + 1, max_p + 1):
            items = _fetch_anue_newslist(cat, page)
            if not items:
                break
            cached.append(items)

    def _iter_all_items(max_p: int):
        """依序 yield (category, item)，跨分類、跨頁。"""
        for cat in categories:
            _ensure_pages(cat, max_p)
            for page_data in page_cache[cat]:
                yield from page_data

    # ── 第一輪：stock 欄位精準比對 ──────────────────────────────────────────
    candidates: list[dict] = []
    seen_ids: set[str] = set()

    for item in _iter_all_items(max_pages):
        if not (search_codes & set(item.get('stock') or [])):
            continue
        news_id = str(item.get('newsId', ''))
        if news_id in seen_ids:
            continue
        news = _anue_item_to_news(item)
        if not news['title']:
            continue
        pub_dt = news.get('_dt')
        if pub_dt and (now_dt - pub_dt) > timedelta(days=180):
            continue
        seen_ids.add(news_id)
        candidates.append(news)
        if len(candidates) >= limit:
            break

    # ── 第二/三輪：關鍵字補足 ─────────────────────────────────────────────────
    if len(candidates) < limit:
        kw_set = {code}
        if zh_name and _is_chinese(zh_name):
            kw_set.add(zh_name)

        # 第二輪用已快取頁，第三輪擴大抓更多頁
        for round_max_p in (max_pages, max_pages * 2):
            for item in _iter_all_items(round_max_p):
                news_id = str(item.get('newsId', ''))
                if news_id in seen_ids:
                    continue
                # 第二/三輪只比對標題，避免 content 帶入毫無關聯的文章
                title_clean = re.sub(r'<[^>]+>', '', item.get('title') or '')
                if not any(kw in title_clean for kw in kw_set):
                    continue
                news = _anue_item_to_news(item)
                if not news['title']:
                    continue
                pub_dt = news.get('_dt')
                if pub_dt and (now_dt - pub_dt) > timedelta(days=180):
                    continue
                seen_ids.add(news_id)
                candidates.append(news)
                if len(candidates) >= limit:
                    break
            if len(candidates) >= limit:
                break

    # ── 排序：publishAt 由新到舊 ─────────────────────────────────────────────
    candidates.sort(
        key=lambda x: x.pop('_dt', None) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return candidates[:limit]


_TW_INDEX_TITLE_KEYWORDS = [
    '加權指數', '台股大漲', '台股大跌', '台股暴漲', '台股暴跌',
    '三大法人', '外資買超', '外資賣超', '大盤行情',
    '台股今日', '台股收盤', '台股行情', '台股開盤',
    '集中市場', '指數上漲', '指數下跌', '大盤指數',
]


def _fetch_anue_market_news(limit: int = 5, max_pages: int = 6) -> list:
    """
    指數（^TWII 等）用：從 Anue 台股新聞清單中過濾出與大盤/加權指數相關的文章。
    以標題關鍵字判斷，翻頁直到湊滿 limit 篇或達到 max_pages。
    """
    candidates = []
    for page in range(1, max_pages + 1):
        try:
            url = (f'https://api.cnyes.com/media/api/v1/newslist/category/tw_stock'
                   f'?limit=30&page={page}')
            r = requests.get(url, timeout=10, headers=_ANUE_NEWSLIST_HEADERS)
            if r.status_code != 200:
                break
            data = r.json().get('items', {}).get('data', [])
            if not data:
                break
            for item in data:
                news = _anue_item_to_news(item)
                title = news.get('title', '')
                if not title:
                    continue
                if any(kw in title for kw in _TW_INDEX_TITLE_KEYWORDS):
                    news.pop('_dt', None)
                    candidates.append(news)
                    if len(candidates) >= limit:
                        break
        except Exception:
            break
        if len(candidates) >= limit:
            break
    return candidates[:limit]


class StockChartAPIView(APIView):
    def get(self, request):
        query = request.query_params.get('ticker', '').strip()
        period = request.query_params.get('period', '1y')
        start_date = request.query_params.get('start_date', '').strip()
        end_date = request.query_params.get('end_date', '').strip()

        if not query:
            return Response({'error': '請輸入股票代號或名稱'}, status=400)

        result = _resolve_ticker(query)
        if isinstance(result, dict):
            return Response(result, status=400)
        ticker = result

        try:
            stock = yf.Ticker(ticker)
            if start_date and end_date:
                from datetime import date as _date, timedelta, datetime as _dt
                # yfinance's end is exclusive; also today's data may not be available yet,
                # so clamp end to yesterday to avoid empty results.
                parsed_end = _dt.strptime(end_date, '%Y-%m-%d').date()
                safe_end = min(parsed_end, _date.today() - timedelta(days=1))
                hist = stock.history(start=start_date, end=safe_end.isoformat())
                if hist.empty:
                    # fallback: fetch 1 year so we at least get the start price
                    hist = stock.history(period='1y')
            else:
                if period not in ('1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'max'):
                    period = '1y'
                hist = stock.history(period=period)

            if hist.empty:
                return Response({'error': f'找不到「{query}」的資料，請確認代號或名稱是否正確'}, status=404)

            data = [
                {'date': date.strftime('%Y-%m-%d'), 'close': round(float(row['Close']), 2)}
                for date, row in hist.iterrows()
                if row['Close'] == row['Close']  # 排除 NaN（NaN != NaN），yfinance 最新一日資料常尚未補齊
            ]

            if not data:
                return Response({'error': f'「{query}」目前無有效股價資料，請稍後再試'}, status=404)

            name = _get_display_name(ticker)
            return Response({'ticker': ticker, 'name': name, 'data': data})

        except Exception as e:
            return Response({'error': str(e)}, status=500)


class StockNewsAPIView(APIView):
    def get(self, request):
        query = request.query_params.get('ticker', '').strip()
        if not query:
            return Response({'error': '請輸入股票代號'}, status=400)

        result = _resolve_ticker(query)
        if isinstance(result, dict):
            # 回傳候選清單或錯誤訊息，讓前端處理
            return Response(result, status=400)

        ticker = result
        name = _get_display_name(ticker)
        kw = _make_keywords(ticker, name)
        code = kw['code']
        is_tw = kw['is_tw']

        zh_name = kw['zh_name']

        LIMIT = 5

        def _supplement_with_google(current: list, gn_query: str, kw_obj: dict) -> list:
            """若 current 不足 LIMIT 篇，用 Google News 補足（去除重複 URL）。"""
            if len(current) >= LIMIT:
                return current
            seen = {n['url'] for n in current}
            gn = _fetch_google_news(gn_query, kw_obj)
            for item in gn:
                if item['url'] not in seen:
                    current.append(item)
                    seen.add(item['url'])
                if len(current) >= LIMIT:
                    break
            return current

        if is_tw:
            tw_code = re.sub(r'\.TWO?$', '', ticker)
            news = _fetch_anue_news_by_stock_code(tw_code, zh_name=zh_name)
            # 不足 LIMIT 時（含 ETF 新聞稀少的情況）補 Google News
            gn_query = f'"{zh_name}" OR "{tw_code}" 股價 OR 營收 OR 財報 OR 法說'
            news = _supplement_with_google(news, gn_query, kw)
        else:
            # 美股 / 指數
            if ticker.startswith('^'):
                news = _fetch_anue_market_news()
                if not news:
                    gn_query = f'"{zh_name}" 股市 OR 指數 OR 大盤'
                    news = _fetch_google_news(gn_query, kw)
            else:
                news = _fetch_anue_news_by_stock_code(code, zh_name=zh_name)
                gn_query = f'"{zh_name}" OR "{code}" 股價 OR 財報 OR 法說 OR 營收'
                news = _supplement_with_google(news, gn_query, kw)

        return Response({'ticker': ticker, 'name': name, 'news': news})


_SCRAPE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
}


def _resolve_google_news_url(url: str) -> str:
    try:
        r = requests.get(url, timeout=8, allow_redirects=True, headers=_SCRAPE_HEADERS)
        if 'news.google.com' not in r.url:
            return r.url
        m = re.search(r'window\.location\s*=\s*["\']([^"\']+)["\']', r.text)
        if m:
            return m.group(1)
        m2 = re.search(r'<meta[^>]+url=([^\s"\'>;]+)', r.text, re.IGNORECASE)
        if m2:
            return m2.group(1)
    except Exception:
        pass
    return url


class NewsContentAPIView(APIView):
    def get(self, request):
        url = request.query_params.get('url', '').strip()
        if not url:
            return Response({'error': '請提供 URL'}, status=400)
        try:
            if 'news.google.com' in url:
                url = _resolve_google_news_url(url)

            if any(d in url for d in _UNSUPPORTED_DOMAINS):
                return Response({'title': '', 'content': '', 'real_url': url})

            r = requests.get(url, timeout=12, allow_redirects=True, headers=_SCRAPE_HEADERS)

            if any(d in r.url for d in _UNSUPPORTED_DOMAINS):
                return Response({'title': '', 'content': '', 'real_url': r.url})

            soup = BeautifulSoup(r.content, 'html.parser')

            content = ''
            if 'news.cnyes.com' in r.url:
                next_script = soup.find('script', {'id': '__NEXT_DATA__'})
                if next_script and next_script.string:
                    try:
                        nd = json.loads(next_script.string)
                        pp = nd.get('props', {}).get('pageProps', {})
                        for key in ('news', 'article', 'data', 'newsData'):
                            item = pp.get(key)
                            if isinstance(item, dict):
                                ch = item.get('content') or item.get('body') or ''
                                if ch:
                                    content = re.sub(r'<[^>]+>', ' ', ch)
                                    content = re.sub(r'\s+', ' ', content).strip()[:4000]
                                    break
                    except Exception:
                        pass

            if not content:
                for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'figure']):
                    tag.decompose()
                container = (
                    soup.find('article') or
                    soup.find(attrs={'class': lambda c: c and any(k in ' '.join(c) for k in ('article-body', 'article-content', 'post-content', 'entry-content', 'story-body', 'content-body'))}) or
                    soup.find('main')
                )
                paragraphs = (container or soup).find_all('p')
                content = '\n\n'.join(
                    p.get_text().strip() for p in paragraphs
                    if len(p.get_text().strip()) > 20
                )[:4000]

            title_el = soup.find('h1') or soup.find('title')
            title = title_el.get_text().strip() if title_el else ''
            if not content:
                return Response({'title': title, 'content': '', 'real_url': r.url})
            content = _translate_content(content)
            title = _translate(title)
            return Response({'title': title, 'content': content, 'real_url': r.url})
        except Exception:
            return Response({'title': '', 'content': '', 'real_url': url})


# ── 情境資產成長模擬（試算計算機新功能）───────────────────────────────────────
# 對應 apps/calculator/services/scenario.py 的已驗證公式 + GBM 模擬。
# ※ 目前仍是「初步校準版本」：參數已用真實資料校準過，但資料庫只涵蓋約15個月的
#   AI供應鏈特殊多頭期，尚未涵蓋空頭/盤整市況，之後資料庫累積更長時間需要重新校準。

class StockTimelineAPIView(APIView):
    """給定 ticker，列出所有已分類過的集數節點（給前端畫時間軸節點用）。"""
    def get(self, request):
        ticker = request.query_params.get('ticker', '').strip()
        if not ticker:
            return Response({'error': '請提供 ticker'}, status=400)

        from apps.summaries.models import StockSentimentScore

        rows = (
            StockSentimentScore.objects
            .using('summariesdb')
            .filter(ticker=ticker, summary__mode='pro')
            .select_related('summary')
            .order_by('summary__published_at')
        )
        nodes = [
            {
                'score_id': r.id,
                'summary_id': r.summary_id,
                'episode_id': r.summary.episode_id,
                'asset_name': r.asset_name,
                'published_at': r.summary.published_at.date().isoformat() if r.summary.published_at else None,
                'podcaster': r.summary.podcaster,
                'macro_score': r.macro_score,
                'risk_score': r.risk_score,
            }
            for r in rows
        ]
        return Response({'ticker': ticker, 'nodes': nodes})


def _parse_months_param(request, default=12, lo=1, hi=60):
    """
    讓GBM模擬的月數跟前端「投資期間」對齊，而不是永遠固定12個月。
    lo/hi 夾住合理範圍（1個月~5年），避免異常輸入讓模擬跑太久或沒有意義。
    """
    raw = request.query_params.get('months', '').strip()
    if not raw:
        return default
    try:
        return max(lo, min(int(raw), hi))
    except ValueError:
        return default


def _parse_base_override_param(request, lo=-0.95, hi=1000.0):
    """
    讓使用者可以自己覆蓋base（年化報酬率，用百分比傳進來，例如?base=25代表25%），
    覆蓋後spread/lean的算法不變（annual_vol/risk_score/macro_score不受影響），
    GBM模擬會以這個新base為中心重新跑一次，而不是直接放棄模擬退回複利曲線。
    lo/hi 用小數（不是百分比）表示，夾住範圍避免base <= -100%讓GBM的log算式出現域錯誤，
    也避免離譜的輸入讓模擬沒有意義。沒帶這個參數就回傳 None，呼叫端會用該筆紀錄自己的歷史base。
    """
    raw = request.query_params.get('base', '').strip()
    if not raw:
        return None
    try:
        pct = float(raw)
    except ValueError:
        return None
    return max(lo, min(pct / 100, hi))


class EnsureEpisodeScoreAPIView(APIView):
    """
    給定 episode_id + ticker：這一集如果已經有這支股票的節目觀點分數就直接回傳，
    沒有的話當場算一次、存進資料庫再回傳（cache-aside，只有第一次點會比較慢，
    之後任何人再選到同一集就跟 stock-timeline 列出來的一樣快）。
    """
    def get(self, request):
        episode_id = request.query_params.get('episode_id', '').strip()
        ticker = request.query_params.get('ticker', '').strip()
        if not episode_id or not ticker:
            return Response({'error': '請提供 episode_id 與 ticker'}, status=400)

        from apps.summaries.models import SummaryRecord
        from apps.summaries.services.sentiment_score import ensure_stock_sentiment_score

        record = (
            SummaryRecord.objects.using('summariesdb')
            .filter(episode_id=episode_id, mode='pro')
            .first()
        )
        if not record:
            return Response({'error': '找不到這一集的正式摘要'}, status=404)

        score = ensure_stock_sentiment_score(record, ticker)
        if not score:
            return Response({'error': '這一集沒有明確、可歸因的個股討論，無法算出這支股票的分數'}, status=422)

        return Response({
            'score_id': score.id,
            'summary_id': score.summary_id,
            'episode_id': record.episode_id,
            'asset_name': score.asset_name,
            'published_at': record.published_at.date().isoformat() if record.published_at else None,
            'podcaster': record.podcaster,
            'macro_score': score.macro_score,
            'risk_score': score.risk_score,
        })


class ScenarioAPIView(APIView):
    """
    給定某一筆 StockSentimentScore（score_id），算出樂觀/基準/悲觀情境 + GBM 區間帶資料。
    只做「當集原始預測」模式；「最新綜合預測」(時間加權) 是獨立的另一個 endpoint。
    """
    def get(self, request):
        score_id = request.query_params.get('score_id', '').strip()
        if not score_id:
            return Response({'error': '請提供 score_id'}, status=400)

        from apps.summaries.models import StockSentimentScore
        from apps.calculator.services.scenario import build_scenario_chart, compute_index_score

        try:
            score = (
                StockSentimentScore.objects.using('summariesdb')
                .select_related('summary')
                .get(id=score_id)
            )
        except StockSentimentScore.DoesNotExist:
            return Response({'error': '找不到這筆分數紀錄'}, status=404)

        if score.base is None or score.annual_vol is None:
            return Response({'error': '這筆紀錄還沒有歷史股價資料'}, status=422)

        months = _parse_months_param(request)
        base_override = _parse_base_override_param(request)
        effective_base = base_override if base_override is not None else score.base
        asof_date = score.summary.published_at

        # 大改版：大盤指數獨立信號，預設關閉（跟現行版本行為完全一樣），
        # 只有明確帶 ?use_index=1 才會多打一次yfinance查大盤、把index_score算進lean。
        use_index = request.query_params.get('use_index', '').strip() in ('1', 'true', 'yes')
        index_momentum = None
        index_score = 0.0
        if use_index:
            index_momentum = _fetch_index_momentum(score.ticker, asof_date)
            if index_momentum is not None:
                index_score = compute_index_score(index_momentum)

        try:
            actual_line = _fetch_actual_price_path(score.ticker, asof_date, months)
        except Exception as e:
            return Response({'error': f'抓不到 {score.ticker} 的股價: {e}'}, status=502)
        start_price = actual_line[0]
        if start_price is None:
            return Response({'error': f'抓不到 {score.ticker} 當時的股價'}, status=502)

        from apps.summaries.services.sentiment_score import find_stock_topics_with_fallback
        topics = find_stock_topics_with_fallback(score.summary)
        matched_topic = next((t for t in topics if t['asset_name'] == score.asset_name), None)
        topic_summary = matched_topic['topic'].get('summary', '') if matched_topic else ''

        # GBM模擬的震盪來源：優先借用這支股票真實的歷史月報酬（bootstrap），
        # 抓不到/歷史不足就自動退回常態隨機數，不會讓請求失敗。
        return_pool = _fetch_historical_return_pool(score.ticker, asof_date)

        chart = build_scenario_chart(
            base=effective_base,
            annual_vol=score.annual_vol,
            risk_score=score.risk_score,
            macro_score=score.macro_score,
            start_price=start_price,
            seed=score.id,  # 同一筆分數每次呼叫畫出來的示範線一致，不會每次刷新都亂跳
            return_pool=return_pool,
            months=months,
            index_score=index_score,
        )

        return Response({
            'asset_name': score.asset_name,
            'ticker': score.ticker,
            'summary_id': score.summary_id,
            'published_at': asof_date.date().isoformat() if asof_date else None,
            'base': score.base,  # 這筆紀錄真實的歷史base，不受base覆蓋影響，給前端顯示「原始數字」用
            'base_overridden': base_override is not None,
            'annual_vol': score.annual_vol,
            'macro_score': score.macro_score,
            'risk_score': score.risk_score,
            'rationale': score.rationale,
            'topic_summary': topic_summary,
            'index_used': use_index,
            'index_momentum': index_momentum,
            'index_score': index_score,
            'start_price': start_price,
            'scenario_returns': {
                'bull': chart.returns.bull,
                'base': chart.returns.base,
                'bear': chart.returns.bear,
            },
            'months': chart.months,
            'bull_line': chart.bull_line,
            'base_line': chart.base_line,
            'bear_line': chart.bear_line,
            'base_band_low': chart.base_band_low,
            'base_band_high': chart.base_band_high,
            'actual_line': actual_line,  # 實際股價路徑，還沒到的月份是 None
            'is_preliminary_calibration': True,  # 前端要用這個標示「初步校準版本」
        })


class WeightedScenarioAPIView(APIView):
    """
    「最新綜合預測」模式：base/annual_vol/start_price 維持選定那一集的真實數字，
    macro_score/risk_score 改用「這一集到最新一集之間，所有討論過這支股票的集數」
    時間加權算出來的綜合值，代入跟 ScenarioAPIView 完全相同的公式 + GBM 邏輯。
    """
    def get(self, request):
        score_id = request.query_params.get('score_id', '').strip()
        if not score_id:
            return Response({'error': '請提供 score_id'}, status=400)

        from apps.summaries.models import StockSentimentScore
        from apps.calculator.services.scenario import build_scenario_chart, compute_time_weighted_scores, compute_index_score

        try:
            score = (
                StockSentimentScore.objects.using('summariesdb')
                .select_related('summary')
                .get(id=score_id)
            )
        except StockSentimentScore.DoesNotExist:
            return Response({'error': '找不到這筆分數紀錄'}, status=404)

        if score.base is None or score.annual_vol is None:
            return Response({'error': '這筆紀錄還沒有歷史股價資料'}, status=422)

        selected_date = score.summary.published_at.date()
        weighted = compute_time_weighted_scores(score.ticker, selected_date)
        if not weighted:
            return Response({'error': '找不到可加權的集數'}, status=422)

        months = _parse_months_param(request)
        base_override = _parse_base_override_param(request)
        effective_base = base_override if base_override is not None else score.base

        use_index = request.query_params.get('use_index', '').strip() in ('1', 'true', 'yes')
        index_momentum = None
        index_score = 0.0
        if use_index:
            index_momentum = _fetch_index_momentum(score.ticker, score.summary.published_at)
            if index_momentum is not None:
                index_score = compute_index_score(index_momentum)

        try:
            actual_line = _fetch_actual_price_path(score.ticker, score.summary.published_at, months)
        except Exception as e:
            return Response({'error': f'抓不到 {score.ticker} 的股價: {e}'}, status=502)
        start_price = actual_line[0]
        if start_price is None:
            return Response({'error': f'抓不到 {score.ticker} 當時的股價'}, status=502)

        return_pool = _fetch_historical_return_pool(score.ticker, score.summary.published_at)

        chart = build_scenario_chart(
            base=effective_base,
            annual_vol=score.annual_vol,
            risk_score=weighted['risk_score'],
            macro_score=weighted['macro_score'],
            start_price=start_price,
            seed=score.id,
            return_pool=return_pool,
            months=months,
            index_score=index_score,
        )

        return Response({
            'asset_name': score.asset_name,
            'ticker': score.ticker,
            'published_at': selected_date.isoformat(),
            'index_used': use_index,
            'index_momentum': index_momentum,
            'index_score': index_score,
            'base': score.base,
            'base_overridden': base_override is not None,
            'annual_vol': score.annual_vol,
            'macro_score': weighted['macro_score'],
            'risk_score': weighted['risk_score'],
            'n_episodes': weighted['n_episodes'],
            'latest_date': weighted['latest_date'],
            'top_contributors': weighted['top_contributors'],
            'start_price': start_price,
            'scenario_returns': {
                'bull': chart.returns.bull,
                'base': chart.returns.base,
                'bear': chart.returns.bear,
            },
            'months': chart.months,
            'bull_line': chart.bull_line,
            'base_line': chart.base_line,
            'bear_line': chart.bear_line,
            'base_band_low': chart.base_band_low,
            'base_band_high': chart.base_band_high,
            'actual_line': actual_line,
            'is_preliminary_calibration': True,
        })


def _add_months(d, months: int):
    """行事曆意義的『加N個月』，處理月份/年份進位跟月底日期溢位（例如1/31加1個月變2/28）。"""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def _fetch_actual_price_path(ticker: str, asof_date, months: int) -> list:
    """
    抓這支股票從節目發布當天開始，實際往後 `months` 個月的真實股價，
    用來跟GBM模擬疊在同一張圖上比較「後來真的發生的」跟「當時模擬出的情境」。
    還沒到的月份（該月份的日期還在未來）回傳 None，不會憑空捏造還沒發生的資料；
    回傳陣列長度是 months+1，跟 build_scenario_chart() 回傳的 months 陣列對齊，
    第0項（發布當天）就是原本 _fetch_price_asof() 要算的起始股價。
    """
    start = asof_date.date() if hasattr(asof_date, 'date') else asof_date
    today = datetime.now(timezone.utc).date()
    target_dates = [_add_months(start, m) for m in range(months + 1)]

    fetch_end = min(target_dates[-1], today) + timedelta(days=8)
    hist = yf.Ticker(ticker).history(
        start=(start - timedelta(days=7)).isoformat(),
        end=fetch_end.isoformat(),
        auto_adjust=True,
    )
    if hist.empty:
        raise ValueError('查無股價資料')

    closes = {ts.date(): float(row['Close']) for ts, row in hist.iterrows()}

    def nearest_close(target, max_lookback=7):
        for back in range(max_lookback + 1):
            d = target - timedelta(days=back)
            if d in closes:
                return closes[d]
        return None

    return [None if d > today else nearest_close(d) for d in target_dates]


def _index_ticker_for(ticker: str) -> str:
    """台股用加權指數，其他（美股為主）用S&P500當大盤基準，草稿規則，之後可以再細分。"""
    return '^TWII' if ticker.endswith('.TW') or ticker.endswith('.TWO') else '^GSPC'


def _fetch_index_momentum(ticker: str, asof_date, lookback_days: int = 90):
    """
    抓對應大盤指數，在asof_date往前lookback_days天的漲跌幅，當作「當時大盤是牛市還是熊市」
    的客觀訊號（用在大改版的 index_score，見 scenario.py 的 M_MAX/compute_index_score）。
    這是獨立、額外的一次yfinance查詢——只有明確要求use_index時才會呼叫，預設路徑不受影響。
    抓不到就回傳 None，呼叫端要把這種情況當「沒有大盤資料，index_score退回中性」處理。
    """
    idx_ticker = _index_ticker_for(ticker)
    start = asof_date.date() if hasattr(asof_date, 'date') else asof_date
    then = start - timedelta(days=lookback_days)
    try:
        hist = yf.Ticker(idx_ticker).history(
            start=(then - timedelta(days=7)).isoformat(),
            end=(start + timedelta(days=1)).isoformat(),
            auto_adjust=True,
        )
    except Exception:
        return None
    if hist.empty:
        return None
    closes = {ts.date(): float(row['Close']) for ts, row in hist.iterrows()}

    def nearest_close(target, max_lookback=7):
        for back in range(max_lookback + 1):
            d = target - timedelta(days=back)
            if d in closes:
                return closes[d]
        return None

    now_close = nearest_close(start)
    then_close = nearest_close(then)
    if now_close is None or then_close is None or then_close <= 0:
        return None
    return now_close / then_close - 1


def _fetch_historical_return_pool(ticker: str, asof_date, years: int = 5, min_months: int = 24):
    """
    抓這支股票在asof_date之前、過去`years`年的月報酬，標準化（減平均、除標準差）後回傳，
    當作GBM模擬震盪的bootstrap樣本池（見 scenario.py 的 _generate_shocks），取代常態隨機數，
    保留這支股票真實發生過的漲跌紋理。用2452筆真實資料回測過，比常態隨機數的區間帶
    覆蓋率略高、平均寬度還略窄，是雙贏。

    只抓 asof_date 之前的資料（yfinance的end參數是exclusive），不會用到發布當時還沒發生的
    未來資訊。歷史不足 min_months 個月就回傳 None，呼叫端會自動退回常態隨機數，不影響原本行為。
    """
    start = asof_date.date() if hasattr(asof_date, 'date') else asof_date
    lo = start - timedelta(days=365 * years + 30)
    try:
        hist = yf.Ticker(ticker).history(start=lo.isoformat(), end=start.isoformat(), auto_adjust=True)
    except Exception:
        return None
    if hist.empty:
        return None

    monthly = hist['Close'].resample('ME').last().dropna()
    vals = monthly.tolist()
    if len(vals) < min_months + 1:
        return None

    log_ret = [math.log(vals[i] / vals[i - 1]) for i in range(1, len(vals)) if vals[i - 1] > 0 and vals[i] > 0]
    if len(log_ret) < min_months:
        return None

    mean_r = sum(log_ret) / len(log_ret)
    var_r = sum((x - mean_r) ** 2 for x in log_ret) / (len(log_ret) - 1)
    if var_r <= 0:
        return None
    std_r = math.sqrt(var_r)
    return [(x - mean_r) / std_r for x in log_ret]
