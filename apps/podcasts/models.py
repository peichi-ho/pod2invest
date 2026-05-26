from django.db import models


class Podcast(models.Model):
    show_name = models.CharField(max_length=255, verbose_name="頻道名稱")

    class Meta:
        db_table = 'podcasts_show'
        app_label = 'podcasts'

    def __str__(self):
        return self.show_name


class PodcastEpisode(models.Model):
    podcast = models.ForeignKey(
        Podcast,
        on_delete=models.CASCADE,
        related_name='episodes',
        verbose_name="所屬頻道",
    )
    episode_title = models.CharField(max_length=255, verbose_name="單集標題")
    audio_url = models.URLField(max_length=500, verbose_name="音檔連結")
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="官方上傳時間")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="寫入時間")

    class Meta:
        db_table = 'podcasts_episode'
        app_label = 'podcasts'

    def __str__(self):
        return f"{self.podcast.show_name} - {self.episode_title}"


class PodcastTranscript(models.Model):
    episode = models.OneToOneField(
        PodcastEpisode,
        on_delete=models.CASCADE,
        related_name='transcript',
        verbose_name="所屬單集",
    )
    srt_content = models.TextField(verbose_name="校對後繁體逐字稿")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="最後校對時間")

    class Meta:
        db_table = 'podcasts_transcript'
        app_label = 'podcasts'

    def __str__(self):
        return f"Transcript: {self.episode.episode_title}"
    