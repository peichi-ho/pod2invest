from django.urls import path
from .views import PreferencesAPIView

urlpatterns = [
    path('preferences/', PreferencesAPIView.as_view(), name='preferences'),
]
