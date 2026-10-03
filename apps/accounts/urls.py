from django.urls import path
from .views import (
    PreferencesAPIView, FavoritesAPIView, FavoriteToggleAPIView,
    FavoriteEpisodesAPIView, FavoriteEpisodeToggleAPIView,
    FavoritePodcastsAPIView, FavoritePodcastToggleAPIView,
    WatchHistoryAPIView, ProfileAPIView, AvatarAPIView, ChangePasswordAPIView,
)

urlpatterns = [
    path('preferences/', PreferencesAPIView.as_view(), name='preferences'),
    path('favorites/', FavoritesAPIView.as_view(), name='favorites'),
    path('favorites/toggle/', FavoriteToggleAPIView.as_view(), name='favorites-toggle'),
    path('favorites/episodes/', FavoriteEpisodesAPIView.as_view(), name='favorites-episodes'),
    path('favorites/episodes/toggle/', FavoriteEpisodeToggleAPIView.as_view(), name='favorites-episodes-toggle'),
    path('favorites/podcasts/', FavoritePodcastsAPIView.as_view(), name='favorites-podcasts'),
    path('favorites/podcasts/toggle/', FavoritePodcastToggleAPIView.as_view(), name='favorites-podcasts-toggle'),
    path('history/', WatchHistoryAPIView.as_view(), name='watch-history'),
    path('profile/', ProfileAPIView.as_view(), name='profile'),
    path('avatar/', AvatarAPIView.as_view(), name='avatar'),
    path('change-password/', ChangePasswordAPIView.as_view(), name='change-password'),
]
