from django.urls import path
from .views import (
    StockChartAPIView, StockNewsAPIView, NewsContentAPIView,
    StockTimelineAPIView, ScenarioAPIView, WeightedScenarioAPIView,
    EnsureEpisodeScoreAPIView,
)
from .preview_views import (
    BootstrapPathPreviewAPIView, EpisodeListPreviewAPIView, EpisodeSourceTextPreviewAPIView,
)

urlpatterns = [
    path('stock-chart/', StockChartAPIView.as_view(), name='stock-chart'),
    path('stock-news/', StockNewsAPIView.as_view(), name='stock-news'),
    path('news-content/', NewsContentAPIView.as_view(), name='news-content'),
    path('stock-timeline/', StockTimelineAPIView.as_view(), name='stock-timeline'),
    path('scenario/', ScenarioAPIView.as_view(), name='scenario'),
    path('scenario-weighted/', WeightedScenarioAPIView.as_view(), name='scenario-weighted'),
    path('ensure-episode-score/', EnsureEpisodeScoreAPIView.as_view(), name='ensure-episode-score'),
    # 獨立預覽用途，跟上面正式的scenario/scenario-weighted完全分開，見preview_views.py
    path('bootstrap-path-preview/', BootstrapPathPreviewAPIView.as_view(), name='bootstrap-path-preview'),
    path('bootstrap-path-preview/episodes/', EpisodeListPreviewAPIView.as_view(), name='bootstrap-path-preview-episodes'),
    path('bootstrap-path-preview/source-text/', EpisodeSourceTextPreviewAPIView.as_view(), name='bootstrap-path-preview-source-text'),
]
