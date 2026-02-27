#資料結構層
from django.db import models


class GlossaryTerm(models.Model):
    term = models.CharField(max_length=100, unique=True)
    short_definition = models.TextField()
    long_definition = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)
    lang = models.CharField(max_length=20, default="zh-TW")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.term


class GlossaryAlias(models.Model):
    term = models.ForeignKey(GlossaryTerm, on_delete=models.CASCADE, related_name="aliases")
    alias = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["term", "alias"], name="uniq_term_alias"),
        ]
        indexes = [
            models.Index(fields=["alias"]),
        ]

    def __str__(self):
        return self.alias
