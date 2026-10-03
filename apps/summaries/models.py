from django.db import models
from django.utils import timezone


class SummaryRecord(models.Model):
    mode = models.CharField(max_length=20)
    source_filename = models.CharField(max_length=255, blank=True)
    # 目前沒有程式碼在讀寫這個欄位，保留不動（決定先不清除，之後有需要再處理）
    model = models.CharField(max_length=100)
    episode = models.ForeignKey(
        "podcasts.PodcastEpisode",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_constraint=False,
        related_name="summaries",
    )
    podcaster = models.CharField(max_length=255, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    one_sentence_summary = models.TextField(blank=True)
    investment_takeaways = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=list, blank=True)
    entities = models.JSONField(default=dict, blank=True)
    arguments = models.JSONField(default=list, blank=True)

    glossary_matches = models.JSONField(default=list, blank=True)
    mind_map = models.JSONField(default=dict, blank=True)
    outlook_calls = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "summaries_summaryrecord"
        app_label = "summaries"

    def __str__(self):
        return f"{self.id} - {self.mode} - {self.source_filename}"


class BacktestingRecord(models.Model):
    RESULT_PENDING = "pending"
    RESULT_PASS    = "pass"
    RESULT_FAIL    = "fail"
    RESULT_SKIP    = "skip"
    RESULT_CHOICES = [
        (RESULT_PENDING, "Pending"),
        (RESULT_PASS,    "Pass"),
        (RESULT_FAIL,    "Fail"),
        (RESULT_SKIP,    "Skip"),
    ]

    summary = models.ForeignKey(
        SummaryRecord,
        on_delete=models.CASCADE,
        db_column="summary_id",
        related_name="backtesting_records",
    )
    episode = models.ForeignKey(
        "podcasts.PodcastEpisode",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        db_constraint=False,
        related_name="backtesting_records",
    )

    # 預測內容
    asset          = models.CharField(max_length=100, default="")
    ticker         = models.CharField(max_length=20, blank=True, default="")
    direction      = models.CharField(max_length=10, default="")
    timeframe_raw  = models.CharField(max_length=50, default="")
    thesis         = models.TextField(blank=True, default="")
    evidence_quote = models.TextField(blank=True, default="")
    evidence_timestamps = models.JSONField(default=list, blank=True)
    target_price   = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # 時間
    start_time = models.DateField(null=True, blank=True)
    end_time   = models.DateField(null=True, blank=True)

    # 價格（每日任務填入）
    start_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    end_price   = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # 結果
    result       = models.CharField(max_length=10, choices=RESULT_CHOICES, default=RESULT_PENDING)
    evaluated_at = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table  = "backtesting"
        app_label = "summaries"

    def __str__(self):
        return f"[{self.direction}] {self.asset} | {self.timeframe_raw} | {self.result}"


class SpeakerAccuracy(models.Model):
    """
    講者預測準確率快照，依講者 × 產業分組。
    sector='' 代表「全體」（所有產業合計）。
    每次執行 refresh_speaker_accuracy 指令時整批重算覆寫。
    """
    podcaster   = models.CharField(max_length=255)
    sector      = models.CharField(max_length=30, blank=True)  # '' = 全體
    pass_count  = models.IntegerField(default=0)
    fail_count  = models.IntegerField(default=0)
    skip_count  = models.IntegerField(default=0)
    evaluatable = models.IntegerField(default=0)   # pass + fail
    accuracy    = models.FloatField(null=True, blank=True)  # None 代表無可評估紀錄
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = "speaker_accuracy"
        app_label       = "summaries"
        unique_together = ("podcaster", "sector")
        ordering        = ["podcaster", "sector"]

    def __str__(self):
        label = self.sector or "全體"
        acc   = f"{self.accuracy*100:.1f}%" if self.accuracy is not None else "N/A"
        return f"{self.podcaster} [{label}] {acc}"


class StockSentimentScore(models.Model):
    """
    某一集節目，對某一支股票的 risk_score / macro_score 判斷（試算計算機用）。
    base / annual_vol 是該股票在這集發布當時的歷史報酬率/波動率，跟分類同時算好存起來，
    避免每次讀取試算頁面都要重新呼叫 yfinance。
    樂觀/基準/悲觀情境不存在這裡，由前端/view 用當下的公式參數即時算出。
    """
    summary = models.ForeignKey(
        SummaryRecord,
        on_delete=models.CASCADE,
        related_name="stock_sentiment_scores",
    )
    episode = models.ForeignKey(
        "podcasts.PodcastEpisode",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        db_constraint=False,
        related_name="stock_sentiment_scores",
    )

    asset_name = models.CharField(max_length=100)
    ticker     = models.CharField(max_length=20)

    # 該股票在這集發布當時的歷史數字（分類當下算好存起來，不即時查）
    base       = models.FloatField(null=True, blank=True)
    annual_vol = models.FloatField(null=True, blank=True)

    # AI 判斷結果
    macro_score = models.FloatField()
    risk_score  = models.FloatField()
    rationale   = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table  = "stock_sentiment_score"
        app_label = "summaries"
        unique_together = ("summary", "asset_name")

    def __str__(self):
        return f"{self.asset_name}({self.ticker}) macro={self.macro_score} risk={self.risk_score}"


class TickerMap(models.Model):
    """股票名稱 → Yahoo Finance ticker 對應表"""
    asset_name = models.CharField(max_length=50, unique=True)   # 台積電 / NVDA
    ticker     = models.CharField(max_length=20)                 # 2330.TW / NVDA
    exchange   = models.CharField(max_length=10, blank=True)     # TWSE / NASDAQ / INDEX
    sector     = models.CharField(max_length=30, blank=True)     # 半導體 / 科技硬體 / 航運 ...
    verified   = models.BooleanField(default=False)              # 人工確認過
    created_at = models.DateTimeField(auto_now_add=True)
    zh_name    = models.CharField(max_length=50, blank=True)

    class Meta:
        db_table  = "ticker_map"
        app_label = "summaries"

    def __str__(self):
        return f"{self.asset_name} → {self.ticker}"


class IndustryClassification(models.Model):
    """
    股票/ETF/指數的產業分類對照表——刻意跟 TickerMap 分開存成獨立一張乾淨的表，
    是給全專案共用查詢的對照表，不是只有summaries這個app自己在用(2026-09-30，
    團隊決定統一分類方法)。TickerMap 要查某檔標的的產業，用ticker來這張表對照，
    不用自己另外存一份分類邏輯或重複判斷。

    分類方法比照 apps/assets 頁面既有的做法(個股詳情頁同一套邏輯)：
      台股：TWSE官方產業別代碼(t187ap03_L開放API)，34種官方分類，權威資料來源
      美股(TWSE查不到才用)：yfinance .info 的 sector/industry(Yahoo自家分類法)，
        翻譯成中文——見 apps/summaries/services/industry_classification.py
    """
    ticker        = models.CharField(max_length=20, unique=True)  # 2330.TW / NVDA
    market        = models.CharField(max_length=10, blank=True)   # TW / US
    industry_code = models.CharField(max_length=10, blank=True)   # TWSE官方代碼，只有台股才有
    industry_name = models.CharField(max_length=50, blank=True)   # 中文產業名稱
    sector_name   = models.CharField(max_length=50, blank=True)   # 中文sector，主要美股才有
    # 這筆分類實際用哪套方法查到的："twse" / "yfinance" / ""(兩邊都查無資料)
    source        = models.CharField(max_length=20, blank=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table  = "industry_classification"
        app_label = "summaries"

    def __str__(self):
        return f"{self.ticker} ({self.market}) → {self.industry_name or '(未分類)'}"


class FinancialReportCache(models.Model):
    """
    財報資料快取（試算計算機 Earnings Simulator / Monte Carlo 用，見
    apps/calculator/services/earnings_simulator.py）。

    跟 apps/knowledge_graph 裡的 FinancialMetricsCache 存的是同一種東西
    (營收/毛利/EPS等原始金額)，故意分開成獨立一張表放在 summariesdb，
    不依賴 knowledge_graphdb 那個常常連線不穩的 Supabase 專案——這裡不是
    要取代那張表，是給 earnings_simulator 一個不受它連線狀況影響的資料源。
    """
    ticker             = models.CharField(max_length=20)
    fiscal_period_end  = models.DateField()
    data_source        = models.CharField(max_length=20)  # "yfinance" | "finmind"
    # 這一期財報「實際公開的日期」，不是期末日——回測時要用這個欄位過濾
    # (disclosure_date <= as_of_date)，避免用到模擬當下其實還沒公告的資料。
    # 抓法跟 apps/knowledge_graph/services/financial_data.py 一致：優先用
    # yfinance t.earnings_dates 的真實公告日，抓不到才退回估計值(季報+45天/
    # 年報+90天)。
    disclosure_date    = models.DateField(null=True, blank=True)

    revenue            = models.FloatField(null=True, blank=True)
    gross_profit       = models.FloatField(null=True, blank=True)
    operating_income   = models.FloatField(null=True, blank=True)
    ebit               = models.FloatField(null=True, blank=True)
    net_income         = models.FloatField(null=True, blank=True)
    shares_outstanding = models.FloatField(null=True, blank=True)
    eps                = models.FloatField(null=True, blank=True)

    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table  = "financial_report_cache"
        app_label = "summaries"
        unique_together = ("ticker", "fiscal_period_end")

    def __str__(self):
        return f"{self.ticker} @ {self.fiscal_period_end}"


