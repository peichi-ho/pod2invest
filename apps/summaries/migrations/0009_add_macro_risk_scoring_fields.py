# 試算頁總經指標：整集層 episode_macro/episode_risk（SummaryRecord），
# 個股層 call_risk（BacktestingRecord）。
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("summaries", "0008_speakeraccuracy"),
    ]

    operations = [
        migrations.AddField(
            model_name="summaryrecord",
            name="episode_macro",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="summaryrecord",
            name="episode_risk",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="backtestingrecord",
            name="call_risk",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
