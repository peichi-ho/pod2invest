# Generated manually on 2026-04-14
# Replaces single podcasts_metadata table with three normalized tables:
#   podcasts_show, podcasts_episode, podcasts_transcript

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('podcasts', '0001_initial'),
    ]

    operations = [
        # 1. 刪除舊的單一大表
        migrations.DeleteModel(
            name='PodcastMetadata',
        ),

        # 2. 建立 Podcast（頻道）
        migrations.CreateModel(
            name='Podcast',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('show_name', models.CharField(max_length=255, verbose_name='頻道名稱')),
            ],
            options={
                'db_table': 'podcasts_show',
            },
        ),

        # 3. 建立 PodcastEpisode（單集）
        migrations.CreateModel(
            name='PodcastEpisode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('podcast', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='episodes',
                    to='podcasts.podcast',
                    verbose_name='所屬頻道',
                )),
                ('episode_title', models.CharField(max_length=255, verbose_name='單集標題')),
                ('audio_url', models.URLField(max_length=500, verbose_name='音檔連結')),
                ('published_at', models.DateTimeField(blank=True, null=True, verbose_name='官方上傳時間')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='寫入時間')),
            ],
            options={
                'db_table': 'podcasts_episode',
            },
        ),

        # 4. 建立 PodcastTranscript（逐字稿）
        migrations.CreateModel(
            name='PodcastTranscript',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('episode', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='transcript',
                    to='podcasts.podcastepisode',
                    verbose_name='所屬單集',
                )),
                ('srt_content', models.TextField(verbose_name='校對後繁體逐字稿')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='最後校對時間')),
            ],
            options={
                'db_table': 'podcasts_transcript',
            },
        ),
    ]
