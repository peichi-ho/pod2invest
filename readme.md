# Pod2Invest

AI-powered Podcast Investment Learning Platform  

Built with Django + Django REST Framework

---

# 📌 Project Structure
 ``` 
POD2INVEST/
│
├── apps/
│ ├── accounts/
│ ├── ai_assistant/
│ ├── calculator/
│ ├── etf/
│ ├── glossary/
│ ├── knowledge_graph/
│ ├── mindmap/
│ ├── podcasts/
│ └── summaries/
│
├── config/
│ ├── settings.py
│ ├── urls.py
│ ├── asgi.py
│ └── wsgi.py
│
├── manage.py
├── requirements.txt
└── README.md
 ``` 

---

# 🚀 First Time Setup 

## 1️⃣ Clone Repository

git clone <your_repo_url>

cd POD2INVEST

## 2️⃣ Create Virtual Environment
Windows

python -m venv .venv

.\.venv\Scripts\activate


Mac

python3 -m venv .venv

source .venv/bin/activate

## 3️⃣ 安裝套件
pip install -r requirements.txt

## 4️⃣ 建立 .env 檔案

在專案根目錄建立 .env

內容範例：

DJANGO_SECRET_KEY=your-secret-key

DJANGO_DEBUG=1

DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost

GEMINI_API_KEY=your-gemini-key

⚠️ 不要把 .env 推上 GitHub

## 5️⃣ 建立資料庫
python manage.py migrate

## 6️⃣ 啟動伺服器
python manage.py runserver

打開瀏覽器：

http://127.0.0.1:8000

## 🌿 開發規範（非常重要）
🔹 不要直接改 main

每個人都要開自己的 branch

範例：

git checkout -b feature/ai-assistant

🔹 開發流程

1.先 pull 最新 main

2.開 feature 

3.開發

4.commit

5.push

6.發 Pull Request

## ⚠️ 禁止事項

1.不要 push .env

2.不要 push .venv

3.不要 push db.sqlite3

4.不要直接改 main
