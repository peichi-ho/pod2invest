from django.contrib import admin
from .models import SummaryRecord


@admin.register(SummaryRecord)
class SummaryRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "mode", "model_name", "source_filename", "created_at")
    search_fields = ("source_filename", "mode", "model_name")
    list_filter = ("mode", "created_at")