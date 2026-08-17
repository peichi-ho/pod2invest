from django.urls import path
from .views import PreferencesAPIView, FavoritesAPIView, FavoriteToggleAPIView

urlpatterns = [
    path('preferences/', PreferencesAPIView.as_view(), name='preferences'),
    path('favorites/', FavoritesAPIView.as_view(), name='favorites'),
    path('favorites/toggle/', FavoriteToggleAPIView.as_view(), name='favorites-toggle'),
]
