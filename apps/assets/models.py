from django.db import models

# 這個 app 沒有自己的 Django model：台股資料即時打 TWSE 開放 API，
# 台股 ETF 資料透過 django.db.connections['etfdb'] 走原生 SQL 查詢，
# 做法跟 apps/etf/services/compare.py 一致。
