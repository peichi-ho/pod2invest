from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("summaries", "0006_add_outlook_calls_to_summaryrecord"),
    ]

    operations = [
        migrations.AddField(
            model_name="tickermap",
            name="sector",
            field=models.CharField(blank=True, max_length=30),
        ),
    ]
