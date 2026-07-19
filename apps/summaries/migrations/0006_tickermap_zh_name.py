# zh_name、sector 欄位在正式資料庫已經手動加過（ticker_map 表），這裡只補上 migration
# 歷史紀錄本身，不對資料庫下任何 DDL，避免 "column already exists" 錯誤。
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("summaries", "0005_sync_backtesting_model"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="tickermap",
                    name="zh_name",
                    field=models.CharField(blank=True, max_length=50),
                ),
                migrations.AddField(
                    model_name="tickermap",
                    name="sector",
                    field=models.CharField(blank=True, max_length=30),
                ),
            ],
            database_operations=[],
        ),
    ]
