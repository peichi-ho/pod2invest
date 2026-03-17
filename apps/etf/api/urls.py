# DRF API routes
from django.urls import path
from .views import ETFCompareAPIView

urlpatterns = [
    path("compare", ETFCompareAPIView.as_view(), name="etf-compare"),
]