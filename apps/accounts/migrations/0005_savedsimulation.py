from django.db import migrations, models

# 試算紀錄(計算機的股價模擬、論點情境試算)。跟 0004 的收藏表一樣：CreateModel 建表，
# 再用 RunSQL 補上對 auth_user 的外鍵(accountsdb 跟 auth_user 所在的 default 是同一顆實體 Postgres，
# 見 0003 的說明)，帳號刪除時一併清掉。


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_avatar_history_and_favorites'),
    ]

    operations = [
        migrations.CreateModel(
            name='SavedSimulation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_id', models.IntegerField(db_index=True)),
                ('module', models.CharField(max_length=10)),
                ('name', models.CharField(max_length=80)),
                ('params', models.JSONField(default=dict)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'saved_simulation',
            },
        ),
        migrations.RunSQL(
            sql="""
                ALTER TABLE saved_simulation
                  ADD CONSTRAINT saved_simulation_user_id_fkey
                  FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE;
            """,
            reverse_sql="""
                ALTER TABLE saved_simulation DROP CONSTRAINT saved_simulation_user_id_fkey;
            """,
        ),
    ]
