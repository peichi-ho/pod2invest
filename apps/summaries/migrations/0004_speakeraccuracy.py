from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("summaries", "0003_consolidate_schema"),
    ]

    operations = [
        migrations.CreateModel(
            name="SpeakerAccuracy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("podcaster",   models.CharField(max_length=255)),
                ("sector",      models.CharField(blank=True, max_length=30)),
                ("pass_count",  models.IntegerField(default=0)),
                ("fail_count",  models.IntegerField(default=0)),
                ("skip_count",  models.IntegerField(default=0)),
                ("evaluatable", models.IntegerField(default=0)),
                ("accuracy",    models.FloatField(blank=True, null=True)),
                ("updated_at",  models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "speaker_accuracy",
                "ordering": ["podcaster", "sector"],
                "unique_together": {("podcaster", "sector")},
            },
        ),
    ]
