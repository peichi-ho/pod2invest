from django.urls import path
from .views import GenerateSummaryView

urlpatterns = [
    path("", GenerateSummaryView.as_view(), name="summary-root"),
    path("generate/", GenerateSummaryView.as_view(), name="summary-generate"),
]