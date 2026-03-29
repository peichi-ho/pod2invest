from django.db import models

class PodcastMetadata(models.Model):
    show_name = models.CharField(max_length=255, verbose_name="頻道名稱")
    episode_title = models.CharField(max_length=255, verbose_name="單集標題")
    audio_url = models.URLField(max_length=500, verbose_name="音檔連結")
    srt_content = models.TextField(verbose_name="校對後繁體逐字稿")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'podcasts_metadata' # 確保跟 Supabase 的資料表名稱一致
        app_label = 'podcasts'


    def __str__(self):
        return f"{self.show_name} - {self.episode_title}"