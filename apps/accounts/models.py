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
        managed = False
