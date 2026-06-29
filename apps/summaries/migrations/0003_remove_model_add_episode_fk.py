from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('podcasts', '0002_normalize_tables'),
        ('summaries', '0002_remove_summaryrecord_model_name_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='summaryrecord',
            name='model',
        ),
        migrations.AddField(
            model_name='summaryrecord',
            name='episode',
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='summaries',
                to='podcasts.podcastepisode',
            ),
        ),
    ]
