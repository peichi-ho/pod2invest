# apps/summaries/urls.py
from django.urls import path
from .views import (SummarizeAPIView, GenerateFromPodcastAPIView, SummaryListAPIView,
                    SummaryDetailAPIView, SummaryMindmapAPIView, PodcastersRankingAPIView,
                    BacktestingBySummaryAPIView, AllBacktestingAPIView, SearchAPIView,
                    AccuracyRankingAPIView, PodcastImagesAPIView)

urlpatterns = [
    path("generate/", SummarizeAPIView.as_view(), name="summaries-generate"),
    path("generate_from_podcast/", GenerateFromPodcastAPIView.as_view(), name="summaries-generate-from-podcast"),
    path("search/", SearchAPIView.as_view(), name="summaries-search"),
    path("accuracy-ranking/", AccuracyRankingAPIView.as_view(), name="accuracy-ranking"),
    path("podcast-images/", PodcastImagesAPIView.as_view(), name="podcast-images"),
    path("", SummaryListAPIView.as_view(), name="summaries-list"),
    path("podcasters/", PodcastersRankingAPIView.as_view(), name="podcasters-ranking"),
    path("backtesting/", AllBacktestingAPIView.as_view(), name="all-backtesting"),
    path("<int:pk>/", SummaryDetailAPIView.as_view(), name="summaries-detail"),
    path("<int:pk>/mindmap/", SummaryMindmapAPIView.as_view(), name="summaries-mindmap"),
    path("<int:pk>/backtesting/", BacktestingBySummaryAPIView.as_view(), name="summaries-backtesting"),
]

