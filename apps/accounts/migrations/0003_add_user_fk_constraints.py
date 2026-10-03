from django.db import migrations

# user_profile / favorite_asset 都用 user_id 一般欄位對應 auth_user.id，沒有真正的資料庫層級
# 外鍵約束。accountsdb 實際上跟 default（auth_user 所在）是同一顆實體 Postgres，所以可以直接
# 補上真正的 FK，讓資料庫保證 user_id 一定對得到一個真實帳號，帳號被刪除時也會自動連帶清掉。
#
# 用 RunSQL 而不是 AddConstraint：UserProfile 是 managed=False（表本身不歸 Django migration
# 管理），ORM 層級的 schema operation 對 unmanaged model 不會真的執行，RunSQL 繞過這點直接下
# DDL，兩張表都適用同一種做法，保持一致。


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_favoriteasset'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE user_profile
                  ADD CONSTRAINT user_profile_user_id_fkey
                  FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE;
            """,
            reverse_sql="""
                ALTER TABLE user_profile DROP CONSTRAINT user_profile_user_id_fkey;
            """,
        ),
        migrations.RunSQL(
            sql="""
                ALTER TABLE favorite_asset
                  ADD CONSTRAINT favorite_asset_user_id_fkey
                  FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE;
            """,
            reverse_sql="""
                ALTER TABLE favorite_asset DROP CONSTRAINT favorite_asset_user_id_fkey;
            """,
        ),
    ]
