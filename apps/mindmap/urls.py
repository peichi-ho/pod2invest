from django.urls import path
from .views import health_check, generate_mindmap, generate_from_file

urlpatterns = [
    path("health/", health_check),
    path("generate/", generate_mindmap),
    path("generate-from-file/", generate_from_file),
]
