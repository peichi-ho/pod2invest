# apps/assets/services/etf_classification.py
"""
台股 ETF 詳情頁的「分類」欄位：兩個維度
  strategy_type ── 投資策略型態（市值型／高股息型／主題型／債券型／槓桿反向／商品型）
  theme         ── 產業／主題聚焦（大盤/多元、半導體、科技…；沒有特定產業就用「大盤/多元」）

etf_master（Supabase etfdb）目前連不上，所以先比照 apps/summaries/management/commands/
tag_ticker_sectors.py 的做法，用 symbol 手動維護一份對照表 —— 資料來源是
ticker_map（summariesdb）裡 sector='ETF' 的既有清單。之後 etfdb 恢復、能撈到完整
台股 ETF 清單時，再把這份表擴充到涵蓋所有標的即可，介面（classify_etf）不用變。
"""

STRATEGY_TYPE = {
    "MARKET_CAP": "市值型",
    "HIGH_DIVIDEND": "高股息型",
    "THEMATIC": "主題型",
    "BOND": "債券型",
    "LEVERAGED_INVERSE": "槓桿反向",
    "COMMODITY": "商品型",
}

# symbol（不含 .TW／.TWO 後綴，比照 etf_master.symbol 的格式）→ (strategy_type, theme)
ETF_CLASSIFICATION: dict[str, tuple[str, str]] = {
    # ── 市值型（台股大盤）──────────────────────────────────────────────
    "0050":   ("MARKET_CAP", "大盤/多元"),   # 元大台灣50
    "006208": ("MARKET_CAP", "大盤/多元"),   # 富邦台50
    "00922":  ("MARKET_CAP", "大盤/多元"),   # 國泰台灣領袖50

    # ── 高股息型 ──────────────────────────────────────────────────────
    "0056":  ("HIGH_DIVIDEND", "大盤/多元"),   # 元大高股息
    "00878": ("HIGH_DIVIDEND", "大盤/多元"),   # 國泰永續高股息
    "00713": ("HIGH_DIVIDEND", "大盤/多元"),   # 元大高息低波
    "00918": ("HIGH_DIVIDEND", "大盤/多元"),   # 大華優利高填息30
    "00919": ("HIGH_DIVIDEND", "大盤/多元"),   # 群益台灣精選高息
    "00940": ("HIGH_DIVIDEND", "大盤/多元"),   # 元大台灣價值高息

    # ── 跨境市值型（海外掛牌，仍是市值加權大盤指數）──────────────────────
    "SPY": ("MARKET_CAP", "大盤/多元"),   # 標普500 ETF
    "VOO": ("MARKET_CAP", "大盤/多元"),   # 先鋒標普500
    "QQQ": ("MARKET_CAP", "科技"),        # 那斯達克100，成分股高度集中科技股
    "EEM": ("MARKET_CAP", "新興市場"),     # 新興市場 ETF

    # ── 債券型 ────────────────────────────────────────────────────────
    "TLT": ("BOND", "債券/固定收益"),   # 美債20年期 ETF
}


def classify_etf(symbol: str) -> dict | None:
    """回傳 {'strategy_type': ..., 'theme': ...}（皆為中文顯示字串）；查無資料回傳 None。"""
    entry = ETF_CLASSIFICATION.get(symbol.upper())
    if not entry:
        return None
    strategy_key, theme = entry
    return {
        "strategy_type": STRATEGY_TYPE[strategy_key],
        "theme": theme,
    }
