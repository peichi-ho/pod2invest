# apps/summaries/urls.py
from django.urls import path
from .views import SummarizeAPIView, GenerateFromPodcastAPIView

urlpatterns = [
    path("generate/", SummarizeAPIView.as_view(), name="summaries-generate"),
    path("generate_from_podcast/", GenerateFromPodcastAPIView.as_view(), name="summaries-generate-from-podcast"),
]