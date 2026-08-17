# 修正資料庫被「幽靈 migration」重新加回的欄位。
#
# 2026-07-31 的 0006_remove_backtestingrecord_call_risk_and_more 已經刪過
# episode_macro / episode_risk（summaries_summaryrecord）跟 call_risk（backtesting）。
# 但 2026-08-06 有另一個環境（models.py 版本落後，還留著這三個欄位定義）跑了
# makemigrations/migrate，產生了兩個本地端從未存在過的 migration 檔案
# （0006_add_missing_episode_macro_risk、0007_add_missing_call_risk），
# 直接對這個共用的正式資料庫把欄位加了回去——這兩個檔案沒有進版控，
# 所以 Django 的一般 makemigrations 偵測不到「欄位又跑回來了」這件事
# （它只看本地 migration 檔案歷史，不是資料庫的即時欄位），必須用 RunSQL 直接處理。
#
# 已確認：這三個欄位重新出現後從未被任何程式碼寫入，全部維持在預設空值
# （episode_macro/episode_risk = {}，call_risk = {}），刪除不會遺失任何資料。
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("summaries", "0007_backtestingrecord_evidence_timestamps"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                'ALTER TABLE summaries_summaryrecord DROP COLUMN IF EXISTS episode_macro;',
                'ALTER TABLE summaries_summaryrecord DROP COLUMN IF EXISTS episode_risk;',
                'ALTER TABLE backtesting DROP COLUMN IF EXISTS call_risk;',
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
