from django.urls import path
from . import views

urlpatterns = [
    path("graph/",                    views.graph_page),
    path("hot-nodes/",                views.hot_nodes_api),
    path("graph-data/",               views.graph_data_api),
    path("industries/",               views.industries_api),
    path("graph-narrative/",          views.graph_narrative_api),
    path("generate/",                 views.generate_graph),
    path("generate-from-summaries/",  views.generate_graph_from_summaries),
]
