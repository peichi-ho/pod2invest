from django.db import models

# 這個 app 沒有自己的 Django model：台股個股／ETF 的價量都即時打 TWSE 開放 API
# （見 services/twse_market_data.py），ETF 基本資料（投資類型/追蹤指數/資產規模/
# 近一年報酬）改成即時查 yfinance + TWSE 開放 API（見 services/basic_info.py、
# services/etf_twse_meta.py）——etfdb（Supabase）已經掛掉，不再依賴它。
# apps/etf/services/compare.py 仍在用 django.db.connections['etfdb']，跟這個 app 無關。
