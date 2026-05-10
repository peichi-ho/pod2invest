from django.db import models
from django.utils import timezone


class SummaryRecord(models.Model):
    mode = models.CharField(max_length=20)
    source_filename = models.CharField(max_length=255, blank=True)
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


class TickerMap(models.Model):
    """股票名稱 → Yahoo Finance ticker 對應表"""
    asset_name = models.CharField(max_length=50, unique=True)   # 台積電 / NVDA
    ticker     = models.CharField(max_length=20)                 # 2330.TW / NVDA
    exchange   = models.CharField(max_length=10, blank=True)     # TWSE / NASDAQ / INDEX
    sector     = models.CharField(max_length=30, blank=True)     # 半導體 / 科技硬體 / 航運 ...
    verified   = models.BooleanField(default=False)              # 人工確認過
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table  = "ticker_map"
        app_label = "summaries"

    def __str__(self):
        return f"{self.asset_name} → {self.ticker}"


