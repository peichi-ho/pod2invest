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

    class Meta:
        app_label = 'accounts'
        db_table = 'user_profile'
        # 原本這裡是 managed=False（假設表已經存在於外部資料庫）。但這個 accountsdb
        # 是全新建立的專案，沒有任何外部腳本會建這張表，所以改成讓 Django migration
        # 自己管理建表，否則 onboarding 偏好設定會因為表不存在而一直 500。


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
