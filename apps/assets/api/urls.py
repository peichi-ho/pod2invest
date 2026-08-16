# DRF API routes
from django.urls import path
from .views import AssetRankingAPIView, AssetBasicInfoAPIView

urlpatterns = [
    path('rankings/', AssetRankingAPIView.as_view(), name='asset-rankings'),
    path('basic-info/', AssetBasicInfoAPIView.as_view(), name='asset-basic-info'),
]
