from django.urls import path
from .views import (
    StockChartAPIView, StockNewsAPIView, NewsContentAPIView,
    StockTimelineAPIView, ScenarioAPIView, WeightedScenarioAPIView,
    EnsureEpisodeScoreAPIView,
)

urlpatterns = [
    path('stock-chart/', StockChartAPIView.as_view(), name='stock-chart'),
    path('stock-news/', StockNewsAPIView.as_view(), name='stock-news'),
    path('news-content/', NewsContentAPIView.as_view(), name='news-content'),
    path('stock-timeline/', StockTimelineAPIView.as_view(), name='stock-timeline'),
    path('scenario/', ScenarioAPIView.as_view(), name='scenario'),
    path('scenario-weighted/', WeightedScenarioAPIView.as_view(), name='scenario-weighted'),
    path('ensure-episode-score/', EnsureEpisodeScoreAPIView.as_view(), name='ensure-episode-score'),
]
