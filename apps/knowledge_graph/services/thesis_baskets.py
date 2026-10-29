"""
apps/knowledge_graph/services/thesis_baskets.py

指數、商品類驗證句的「代理公司籃子」。

知識圖譜只收公司節點，「台股加權指數會反彈」「油價下半年走高」這類驗證句在圖譜裡
找不到對應節點。thesis_score.py 會先照原本方式找公司節點，找不到時才查這裡：
有籃子定義的指數／原油，改用成分股（或相關公司）的證據加權計算；
其他非公司標的（黃金、白銀、比特幣、匯率、總經）只用說話者準確率。

權重是約略的指數市值權重（2026 年參考值），不需要很精確——用途是讓台積電在
台股大盤的影響力約四成、不會因為某檔小型股剛好被討論很多次就主導整個大盤分數。
指數成分大幅調整時再更新即可。

sign：該公司訊號對標的的方向。原油籃子裡生產者 +1（油價漲受惠），
航空公司 -1（油價漲是成本壓力）；指數成分股一律 +1。
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Constituent:
    ticker: str             # 對應 TickerMap / 知識圖譜分組用的 ticker
    names: tuple[str, ...]  # 知識圖譜裡可能的節點名稱（ticker 分組沒涵蓋到時用名稱比對補上）
    weight: float
    sign: int = 1


@dataclass(frozen=True)
class Basket:
    label: str
    constituents: tuple[Constituent, ...]


def _c(ticker, *names, weight, sign=1):
    return Constituent(ticker=ticker, names=names, weight=weight, sign=sign)


BASKETS: dict[str, Basket] = {
    "^TWII": Basket("台股加權指數", (
        _c("2330.TW", "台積電", weight=42), _c("2317.TW", "鴻海", weight=5),
        _c("2454.TW", "聯發科", weight=4), _c("2308.TW", "台達電", weight=3),
        _c("2382.TW", "廣達", weight=2.2), _c("2881.TW", "富邦金", weight=1.6),
        _c("2891.TW", "中信金", weight=1.5), _c("2882.TW", "國泰金", weight=1.3),
        _c("3711.TW", "日月光投控", "日月光", weight=1.3), _c("2412.TW", "中華電", "中華電信", weight=1.1),
        _c("6669.TW", "緯穎", weight=1.0), _c("2345.TW", "智邦", weight=1.0),
        _c("2303.TW", "聯電", weight=0.9), _c("2886.TW", "兆豐金", weight=0.9),
        _c("2884.TW", "玉山金", weight=0.8), _c("3017.TW", "奇鋐", weight=0.8),
        _c("2383.TW", "台光電", weight=0.7), _c("3231.TW", "緯創", weight=0.7),
        _c("2885.TW", "元大金", weight=0.7), _c("2357.TW", "華碩", weight=0.6),
    )),
    "^TWOTCI": Basket("櫃買指數", (
        _c("5274.TWO", "信驊", weight=10), _c("6488.TWO", "環球晶", weight=9),
        _c("5347.TWO", "世界先進", "世界", weight=7), _c("8299.TWO", "群聯", weight=6),
        _c("3105.TWO", "穩懋", weight=5), _c("8069.TWO", "元太", weight=5),
        _c("3529.TWO", "力旺", weight=5), _c("3293.TWO", "鈊象", weight=4),
        _c("6274.TWO", "台燿", weight=4), _c("3324.TWO", "雙鴻", weight=4),
    )),
    "^GSPC": Basket("S&P 500", (
        _c("NVDA", "輝達", weight=7.5), _c("AAPL", "蘋果", weight=6.5), _c("MSFT", "微軟", weight=6.5),
        _c("AMZN", "亞馬遜", weight=4), _c("GOOGL", "Google", weight=4), _c("META", "Meta", weight=3),
        _c("AVGO", "博通", weight=2.5), _c("TSLA", "特斯拉", weight=2), _c("BRK-B", "波克夏", weight=1.6),
        _c("JPM", "摩根大通", weight=1.4), _c("LLY", "禮來", weight=1.2), _c("ORCL", "甲骨文", weight=1.0),
        _c("V", "Visa", weight=1.0), _c("NFLX", "Netflix", weight=0.9), _c("XOM", "埃克森美孚", "ExxonMobil", weight=0.9),
        _c("WMT", "沃爾瑪", weight=0.9), _c("COST", "Costco", "好市多", weight=0.8),
    )),
    "^IXIC": Basket("那斯達克", (
        _c("NVDA", "輝達", weight=9), _c("MSFT", "微軟", weight=8), _c("AAPL", "蘋果", weight=8),
        _c("AMZN", "亞馬遜", weight=5.5), _c("AVGO", "博通", weight=5), _c("GOOGL", "Google", weight=5),
        _c("META", "Meta", weight=4), _c("TSLA", "特斯拉", weight=3), _c("COST", "Costco", "好市多", weight=2.5),
        _c("NFLX", "Netflix", weight=2), _c("AMD", "超微", weight=1.5), _c("PLTR", "Palantir", weight=1.5),
    )),
    "^SOX": Basket("費城半導體指數", (
        _c("NVDA", "輝達", weight=9), _c("AVGO", "博通", weight=8), _c("AMD", "超微", weight=7),
        _c("2330.TW", "台積電", weight=6), _c("MU", "美光", weight=5), _c("QCOM", "高通", weight=4),
        _c("INTC", "英特爾", "Intel", weight=4), _c("TXN", "德州儀器", weight=4), _c("AMAT", "應材", "應用材料", weight=4),
        _c("LRCX", "科林研發", weight=4), _c("KLAC", "科磊", weight=4), _c("ADI", "亞德諾", weight=4),
        _c("MRVL", "Marvell", weight=3), _c("ASML", "艾司摩爾", weight=3),
    )),
    "CL=F": Basket("原油", (
        _c("6505.TW", "台塑化", weight=3), _c("1301.TW", "台塑", weight=2),
        _c("1303.TW", "南亞", weight=1), _c("1326.TW", "台化", weight=1),
        _c("CVX", "雪佛龍", "Chevron", weight=3), _c("XOM", "埃克森美孚", "ExxonMobil", weight=3),
        _c("2610.TW", "中華航空", "華航", weight=1, sign=-1),
    )),
}
# 道瓊是價格加權、成分股跟 S&P 500 大型股高度重疊，直接沿用 S&P 500 籃子
BASKETS["^DJI"] = BASKETS["^GSPC"]

# resolve_ticker() 的關鍵字表沒涵蓋、但實際驗證句常見的寫法
_EXTRA_KEYWORDS = {"WTI": "CL=F"}


def basket_for(asset: str, ticker: Optional[str]) -> Optional[tuple[str, Basket]]:
    """回傳 (籃子 key, Basket)；這個標的沒有籃子定義時回傳 None。"""
    key = _EXTRA_KEYWORDS.get((asset or "").strip().upper()) or ticker
    if key in BASKETS:
        return key, BASKETS[key]
    return None
