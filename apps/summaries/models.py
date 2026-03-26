from django.db import models


class SummaryRecord(models.Model):
    mode = models.CharField(max_length=20)
    model = models.CharField(max_length=100)
    source_filename = models.CharField(max_length=255, blank=True)

    one_sentence_summary = models.TextField(blank=True)
    investment_takeaways = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=list, blank=True)
    entities = models.JSONField(default=dict, blank=True)
    arguments = models.JSONField(default=list, blank=True)
    outlook_calls = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "summaries_summaryrecord"
        app_label = "summaries"

    def __str__(self):
        return f"{self.id} - {self.mode} - {self.source_filename}"