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


