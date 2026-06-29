from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("summaries", "0005_sync_backtesting_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="summaryrecord",
            name="outlook_calls",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
