from django.db import migrations, models

# user_profile 是 managed=False（表本身不歸 Django migration 管理），ORM 層級的
# schema operation 對 unmanaged model 不會真的執行，所以新增 avatar_base64 欄位
# 用 RunSQL 直接下 DDL（跟 0003_add_user_fk_constraints 對 FK 約束的做法一致）。
#
# 新的三張表（favorite_episode / favorite_podcast / watch_history）都是全新建立、
# 沒有既有資料，交給 CreateModel 建表；user_id 一樣沒有真正的外鍵（accountsdb 跟
# auth_user 所在的 default 實際上是同一顆實體 Postgres，見 0003 的說明），另外用
# RunSQL 補上 FK 約束，保證 user_id 一定對得到一個真實帳號。


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_add_user_fk_constraints'),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE user_profile ADD COLUMN avatar_base64 TEXT NOT NULL DEFAULT '';",
            reverse_sql="ALTER TABLE user_profile DROP COLUMN avatar_base64;",
        ),
        migrations.CreateModel(
            name='FavoriteEpisode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_id', models.IntegerField()),
                ('summary_id', models.IntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'favorite_episode',
                'unique_together': {('user_id', 'summary_id')},
            },
        ),
        migrations.CreateModel(
            name='FavoritePodcast',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_id', models.IntegerField()),
                ('podcaster', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'favorite_podcast',
                'unique_together': {('user_id', 'podcaster')},
            },
        ),
        migrations.CreateModel(
            name='WatchHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_id', models.IntegerField()),
                ('summary_id', models.IntegerField()),
                ('viewed_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'watch_history',
                'unique_together': {('user_id', 'summary_id')},
            },
        ),
        migrations.RunSQL(
            sql="""
                ALTER TABLE favorite_episode
                  ADD CONSTRAINT favorite_episode_user_id_fkey
                  FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE;
            """,
            reverse_sql="""
                ALTER TABLE favorite_episode DROP CONSTRAINT favorite_episode_user_id_fkey;
            """,
        ),
        migrations.RunSQL(
            sql="""
                ALTER TABLE favorite_podcast
                  ADD CONSTRAINT favorite_podcast_user_id_fkey
                  FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE;
            """,
            reverse_sql="""
                ALTER TABLE favorite_podcast DROP CONSTRAINT favorite_podcast_user_id_fkey;
            """,
        ),
        migrations.RunSQL(
            sql="""
                ALTER TABLE watch_history
                  ADD CONSTRAINT watch_history_user_id_fkey
                  FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE;
            """,
            reverse_sql="""
                ALTER TABLE watch_history DROP CONSTRAINT watch_history_user_id_fkey;
            """,
        ),
    ]
