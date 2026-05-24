from django.urls import path
from . import views

urlpatterns = [
    path("graph/",                    views.graph_page),
    path("graph-diff/",               views.graph_diff_page),
    path("claim-analysis/",           views.claim_analysis_page),
    path("latest-date/",              views.latest_date_api),
    path("hot-nodes/",                views.hot_nodes_api),
    path("graph-data/",               views.graph_data_api),
    path("graph-diff-data/",          views.graph_diff_api),
    path("graph-diff-narrative/",     views.graph_diff_narrative_api),
    path("industries/",               views.industries_api),
    path("graph-narrative/",          views.graph_narrative_api),
    path("episodes/",                 views.episodes_api),
    path("analyze-episode/",          views.analyze_episode_api),
    path("generate/",                 views.generate_graph),
    path("generate-from-summaries/",  views.generate_graph_from_summaries),
    path("generate-narrative/",       views.generate_narrative_graph),
]
