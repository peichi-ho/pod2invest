# 手動整併：取代原本分裂成兩條分支(0003~0007)的 migration 歷史。
# 兩條分支各自獨立建立過一次 TickerMap、一次 BacktestingRecord，導致任何全新環境
# 執行 migrate 都會撞到「資料表已存在」的衝突。這份檔案改成只建立一次，
# 並直接對齊真實資料庫(Supabase summariesdb)目前的實際欄位狀態。
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("podcasts", "0002_normalize_tables"),
        ("summaries", "0002_remove_summaryrecord_model_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="summaryrecord",
            name="episode",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="summaries",
                to="podcasts.podcastepisode",
            ),
        ),
        migrations.AddField(
            model_name="summaryrecord",
            name="podcaster",
            field=models.CharField(blank=True, default="", max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="summaryrecord",
            name="published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="summaryrecord",
            name="glossary_matches",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="summaryrecord",
            name="mind_map",
            field=models.JSONField(blank=True, default=dict),
        ),
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
        migrations.CreateModel(
            name="TickerMap",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("asset_name", models.CharField(max_length=50, unique=True)),
                ("ticker", models.CharField(max_length=20)),
                ("exchange", models.CharField(blank=True, max_length=10)),
                ("sector", models.CharField(blank=True, max_length=30)),
                ("verified", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("zh_name", models.CharField(blank=True, max_length=50)),
            ],
            options={"db_table": "ticker_map", "app_label": "summaries"},
        ),
        migrations.CreateModel(
            name="BacktestingRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("asset", models.CharField(default="", max_length=100)),
                ("ticker", models.CharField(blank=True, default="", max_length=20)),
                ("direction", models.CharField(default="", max_length=10)),
                ("timeframe_raw", models.CharField(default="", max_length=50)),
                ("thesis", models.TextField(blank=True, default="")),
                ("evidence_quote", models.TextField(blank=True, default="")),
                ("target_price", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("start_time", models.DateField(blank=True, null=True)),
                ("end_time", models.DateField(blank=True, null=True)),
                ("start_price", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("end_price", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                (
                    "result",
                    models.CharField(
                        choices=[("pending", "Pending"), ("pass", "Pass"), ("fail", "Fail"), ("skip", "Skip")],
                        default="pending",
                        max_length=10,
                    ),
                ),
                ("evaluated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("call_risk", models.JSONField(blank=True, default=dict)),
                (
                    "episode",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="backtesting_records",
                        to="podcasts.podcastepisode",
                    ),
                ),
                (
                    "summary",
                    models.ForeignKey(
                        db_column="summary_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="backtesting_records",
                        to="summaries.summaryrecord",
                    ),
                ),
            ],
            options={"db_table": "backtesting", "app_label": "summaries"},
        ),
    ]
