from django.db import models


class SummaryRecord(models.Model):
    MODE_CHOICES = [
        ("novice", "Novice"),
        ("pro", "Pro"),
        ("both", "Both"),
    ]

    mode = models.CharField(max_length=10, choices=MODE_CHOICES)
    model_name = models.CharField(max_length=100, blank=True, default="")
    source_filename = models.CharField(max_length=255, blank=True, null=True)
    srt_text = models.TextField(blank=True, default="")
    result_json = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.id} - {self.mode} - {self.created_at:%Y-%m-%d %H:%M:%S}"