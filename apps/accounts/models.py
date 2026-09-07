from django.db import models
from django.contrib.postgres.fields import ArrayField


class UserProfile(models.Model):
    user_id = models.IntegerField(unique=True)
    level = models.CharField(max_length=20, blank=True)
    markets = ArrayField(models.CharField(max_length=20), default=list, blank=True)
    style = models.CharField(max_length=20, blank=True)
    goal = models.CharField(max_length=20, blank=True)
    capital = models.CharField(max_length=10, blank=True)
    onboarding_done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # data URI（"data:image/png;base64,...."），沒設定就是空字串 → 前端顯示預設頭像。
    # 用 base64 直接存文字欄位而不是檔案／MEDIA_ROOT：專案目前沒有設定檔案伺服器，
    # 部署環境也不確定有沒有持久化硬碟，base64 存進資料庫不受影響。
    avatar_base64 = models.TextField(blank=True, default='')

    class Meta:
        app_label = 'accounts'
        db_table = 'user_profile'
        managed = False


class FavoriteAsset(models.Model):
    """
    使用者收藏的標的（ASSETS 頁面用）。跟 UserProfile 一樣用 user_id 一般欄位、
    不是 ForeignKey(User)——accountsdb 是完全獨立的資料庫，Django 沒辦法跨資料庫
    做外鍵約束。這是新建的表（不像 UserProfile 是既有外部表），所以維持
    managed=True，交給 migration 自己建表。
    """
    user_id = models.IntegerField()
    category = models.CharField(max_length=20)   # 'tw_stock' | 'tw_etf'，跟 apps/assets 對齊
    symbol = models.CharField(max_length=20)      # 不含 .TW 後綴，跟 rankings API 的 row['symbol'] 一致
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'accounts'
        db_table = 'favorite_asset'
        unique_together = ('user_id', 'category', 'symbol')


class FavoriteEpisode(models.Model):
    """
    使用者收藏的單集（Profile 頁「Saved Insights」用）。summary_id 對應
    summariesdb 的 SummaryRecord.id——跟前端 openDeepDive(summaryId) 用的是同一個
    id，不是 podcasts.PodcastEpisode.id（同一集可能有 novice/pro 兩筆不同的
    SummaryRecord，收藏是收使用者實際點開的那一筆）。summariesdb 是獨立資料庫，
    沒辦法建立跨庫外鍵，跟 FavoriteAsset 一樣用一般整數欄位存。
    """
    user_id = models.IntegerField()
    summary_id = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'accounts'
        db_table = 'favorite_episode'
        unique_together = ('user_id', 'summary_id')


class FavoritePodcast(models.Model):
    """
    使用者收藏（追蹤）的節目（Profile 頁「最愛 Podcast」用）。用 podcaster 名稱字串
    對應，跟 rankings.js/podcaster.html 全專案用節目名稱字串當 key 的慣例一致，
    不用 podcasts.Podcast.id（那是另一顆資料庫，一樣沒辦法建跨庫外鍵）。
    """
    user_id = models.IntegerField()
    podcaster = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'accounts'
        db_table = 'favorite_podcast'
        unique_together = ('user_id', 'podcaster')


class WatchHistory(models.Model):
    """
    使用者的單集觀看紀錄（Profile 頁用）。每次打開單集頁面（deep_dive）就會
    upsert 一筆，同一集重複打開只更新 viewed_at，不會一直往下累加重複項目。
    """
    user_id = models.IntegerField()
    summary_id = models.IntegerField()
    viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'accounts'
        db_table = 'watch_history'
        unique_together = ('user_id', 'summary_id')
