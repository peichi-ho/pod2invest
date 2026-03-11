# apps/summaries/urls.py
from django.urls import path
from .views import SummarizeAPIView

urlpatterns = [
    path("generate/", SummarizeAPIView.as_view(), name="summaries-generate"),
]