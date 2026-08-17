from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge_graph', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='FinancialMetricsCache',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ticker', models.TextField()),
                ('fiscal_period_end', models.DateField()),
                ('data_source', models.TextField()),
                ('disclosure_date', models.DateField(null=True)),
                ('total_assets', models.FloatField(null=True)),
                ('current_assets', models.FloatField(null=True)),
                ('current_liabilities', models.FloatField(null=True)),
                ('total_liabilities', models.FloatField(null=True)),
                ('working_capital', models.FloatField(null=True)),
                ('retained_earnings', models.FloatField(null=True)),
                ('ebit', models.FloatField(null=True)),
                ('revenue', models.FloatField(null=True)),
                ('gross_profit', models.FloatField(null=True)),
                ('operating_income', models.FloatField(null=True)),
                ('net_income', models.FloatField(null=True)),
                ('operating_cash_flow', models.FloatField(null=True)),
                ('shares_outstanding', models.FloatField(null=True)),
                ('eps', models.FloatField(null=True)),
                ('fetched_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'financial_metrics_cache',
            },
        ),
        migrations.AddIndex(
            model_name='financialmetricscache',
            index=models.Index(fields=['ticker', 'fiscal_period_end'], name='financial_m_ticker_7175f8_idx'),
        ),
        migrations.AddConstraint(
            model_name='financialmetricscache',
            constraint=models.UniqueConstraint(fields=('ticker', 'fiscal_period_end'), name='uniq_fin_metrics_ticker_period'),
        ),
    ]
