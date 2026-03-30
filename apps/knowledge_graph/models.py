from django.db import models
class KnowledgeGraphNode(models.Model):
    name = models.TextField(primary_key=True)
    industry = models.TextField(blank=True)

    class Meta:
        db_table = "nodes"
        app_label = "knowledge_graph"
        managed = False

class KnowledgeGraphLink(models.Model):
    source = models.TextField()
    target = models.TextField()
    relation_type = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    summary_date = models.DateField(null=True)
    podcast_source = models.TextField(blank=True)

    class Meta:
        db_table = "links"
        app_label = "knowledge_graph"
        managed = False

# Create your models here.
