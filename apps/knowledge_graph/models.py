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


class FinancialMetricsCache(models.Model):
    """
    Layer 2 財務基本面快取，以「ticker + 財報期末日」為單位，避免重複打
    yfinance/FinMind。data_source 記錄這筆資料實際是從哪個來源抓到的——
    同一筆（同一 ticker、同一期）的所有欄位一定來自同一個 source，不會
    把 yfinance 的分子跟 FinMind 的分母湊在一起用（兩邊對同一會計項目的
    認列範圍不一定完全一致，混用會做出看起來正常、實際上基準不一致的比率）。
    """
    ticker = models.TextField()
    fiscal_period_end = models.DateField()
    data_source = models.TextField()  # "yfinance" | "finmind"
    disclosure_date = models.DateField(null=True)  # 實際公告日；查不到時為 None

    total_assets = models.FloatField(null=True)
    current_assets = models.FloatField(null=True)
    current_liabilities = models.FloatField(null=True)
    total_liabilities = models.FloatField(null=True)
    working_capital = models.FloatField(null=True)
    retained_earnings = models.FloatField(null=True)
    ebit = models.FloatField(null=True)
    revenue = models.FloatField(null=True)
    gross_profit = models.FloatField(null=True)
    operating_income = models.FloatField(null=True)
    net_income = models.FloatField(null=True)
    operating_cash_flow = models.FloatField(null=True)
    shares_outstanding = models.FloatField(null=True)
    eps = models.FloatField(null=True)

    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "financial_metrics_cache"
        app_label = "knowledge_graph"
        constraints = [
            models.UniqueConstraint(
                fields=["ticker", "fiscal_period_end"],
                name="uniq_fin_metrics_ticker_period",
            ),
        ]
        indexes = [
            models.Index(fields=["ticker", "fiscal_period_end"]),
        ]
