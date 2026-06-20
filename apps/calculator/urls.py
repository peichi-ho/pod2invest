from django.urls import path
from .views import StockChartAPIView, StockNewsAPIView, NewsContentAPIView

urlpatterns = [
    path('stock-chart/', StockChartAPIView.as_view(), name='stock-chart'),
    path('stock-news/', StockNewsAPIView.as_view(), name='stock-news'),
    path('news-content/', NewsContentAPIView.as_view(), name='news-content'),
]
