from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("summaries", "0005_sync_backtesting_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="tickermap",
            name="zh_name",
            field=models.CharField(blank=True, default="", max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="tickermap",
            name="sector",
            field=models.CharField(blank=True, default="", max_length=30),
            preserve_default=False,
        ),
    ]
