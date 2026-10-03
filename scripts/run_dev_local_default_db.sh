#!/bin/bash
# 本機開發用：GLOSSARY_DB(default)所在的Supabase專案被暫停連線時，
# 用這個腳本啟動dev server，讓default改接本機SQLite(見config/settings.py
# 的USE_LOCAL_DEFAULT_DB開關)，其他資料庫不受影響。
#
# 用絕對路徑指定python3(conda base環境)，不依賴PATH——直接用「bash 這個腳本」
# 執行時，bash不會像互動式zsh那樣自動啟用conda，PATH裡的python3可能會變成
# 系統內建的、沒裝過dotenv/django等套件的版本，導致 ModuleNotFoundError。
PYTHON_BIN="/opt/anaconda3/bin/python3"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "找不到 $PYTHON_BIN，改用PATH裡的python3(可能抓到系統版本，缺套件的話要自己啟用conda環境後再跑)"
  PYTHON_BIN="python3"
fi

export USE_LOCAL_DEFAULT_DB=1
cd "$(dirname "$0")/.."
exec "$PYTHON_BIN" manage.py runserver 127.0.0.1:8000
