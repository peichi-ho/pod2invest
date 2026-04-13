from django.db import models


class SummaryRecord(models.Model):
    mode = models.CharField(max_length=20)
    model = models.CharField(max_length=100)
    source_filename = models.CharField(max_length=255, blank=True)
    podcaster = models.CharField(max_length=255, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    one_sentence_summary = models.TextField(blank=True)
    investment_takeaways = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=list, blank=True)
    entities = models.JSONField(default=dict, blank=True)
    arguments = models.JSONField(default=list, blank=True)

    glossary_matches = models.JSONField(default=list, blank=True)
    mind_map = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "summaries_summaryrecord"
        app_label = "summaries"

    def __str__(self):
        return f"{self.id} - {self.mode} - {self.source_filename}"


class BacktestingRecord(models.Model):
    summary = models.ForeignKey(
        SummaryRecord,
        on_delete=models.CASCADE,
        db_column="summary_id",
        related_name="backtesting_records",
    )
    outlook_calls = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "backtesting"
        app_label = "summaries"

    def __str__(self):
        return f"BacktestingRecord(summary_id={self.summary_id})"
