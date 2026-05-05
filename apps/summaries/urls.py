# apps/summaries/urls.py
from django.urls import path
from .views import SummarizeAPIView, GenerateFromPodcastAPIView, SummaryListAPIView, SummaryDetailAPIView, SummaryMindmapAPIView, PodcastersRankingAPIView

urlpatterns = [
    path("generate/", SummarizeAPIView.as_view(), name="summaries-generate"),
    path("generate_from_podcast/", GenerateFromPodcastAPIView.as_view(), name="summaries-generate-from-podcast"),
    path("", SummaryListAPIView.as_view(), name="summaries-list"),
    path("podcasters/", PodcastersRankingAPIView.as_view(), name="podcasters-ranking"),
    path("<int:pk>/", SummaryDetailAPIView.as_view(), name="summaries-detail"),
    path("<int:pk>/mindmap/", SummaryMindmapAPIView.as_view(), name="summaries-mindmap"),
]

