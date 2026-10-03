from django.urls import path
from . import views

urlpatterns = [
    path("annotate/", views.annotate_api),
    path("term/<int:term_id>/", views.term_detail),
]
