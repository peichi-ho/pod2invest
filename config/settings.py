"""
Django settings for config project.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)
sys.path.append(str(BASE_DIR / "apps"))



# 改寫 SECRET_KEY 與 DEBUG
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-預設字串請在.env中覆蓋')
DEBUG = os.getenv('DJANGO_DEBUG', 'True').strip().lower() in ('1', 'true')
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

# Gemini / AI Assistant settings

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
# 知識圖譜關係判斷（extract_v2 步驟 3）用較強的模型，flash-lite 判斷過於保守
KG_RELATION_MODEL = os.getenv("KG_RELATION_MODEL", "gemini-2.5-flash")
VERTEX_PROJECT_ID = os.getenv("VERTEX_PROJECT_ID", "")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")

# GCP 憑證：若 .env 裡是相對路徑，自動轉換為以專案根目錄為基準的絕對路徑
_gcp_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
if _gcp_creds and not os.path.isabs(_gcp_creds):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(BASE_DIR / _gcp_creds)

# 股票資料快取秒數（避免 yfinance 太慢）
STOCK_CACHE_TTL_SECONDS = int(os.getenv("STOCK_CACHE_TTL_SECONDS", "60"))

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'rest_framework',
    'apps.glossary.apps.GlossaryConfig',
    'apps.summaries.apps.SummariesConfig',
    'apps.etf.apps.EtfConfig',
    'apps.podcasts.apps.PodcastsConfig',
    'apps.mindmap.apps.MindmapConfig',
    'apps.ai_assistant.apps.AiAssistantConfig',
    'apps.knowledge_graph.apps.KnowledgeGraphConfig',
    'apps.accounts.apps.AccountsConfig',
    'apps.assets.apps.AssetsConfig',
]


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
     "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("GLOSSARY_DB_NAME", "postgres"),
        "USER": os.getenv("GLOSSARY_DB_USER", ""),
        "PASSWORD": os.getenv("GLOSSARY_DB_PASSWORD", ""),
        "HOST": os.getenv("GLOSSARY_DB_HOST", ""),
        "PORT": os.getenv("GLOSSARY_DB_PORT", "5432"),
        "OPTIONS": {
            "sslmode": "require",
        },
    } if os.getenv("USE_LOCAL_DEFAULT_DB", "").strip().lower() not in ("1", "true") else {
        # 本機開發用：GLOSSARY_DB 所在的 Supabase 專案被鎖時，先用本機 SQLite 頂著
        # default（只有 auth/glossary/mindmap 用得到），不影響 etfdb/summariesdb/
        # podcasts/knowledge_graphdb 等其他資料庫，仍然接真正的 Supabase。
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    },
    # 新增：ETF 專用 PostgreSQL
    "etfdb": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("ETF_DB_NAME", "postgres"),
        "USER": os.getenv("ETF_DB_USER", ""),
        "PASSWORD": os.getenv("ETF_DB_PASSWORD", ""),
        "HOST": os.getenv("ETF_DB_HOST", ""),
        "PORT": os.getenv("ETF_DB_PORT", "5432"),
        "OPTIONS": {"sslmode": "require"},
    },
    "summariesdb": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("SUMMARIES_DB_NAME", "postgres"),
        "USER": os.getenv("SUMMARIES_DB_USER", ""),
        "PASSWORD": os.getenv("SUMMARIES_DB_PASSWORD", ""),
        "HOST": os.getenv("SUMMARIES_DB_HOST", ""),
        "PORT": os.getenv("SUMMARIES_DB_PORT", "5432"),
        "OPTIONS": {
            "sslmode": "require",
        },
    },
    "podcasts": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("PODCASTS_DB_NAME"),
        "USER": os.getenv("PODCASTS_DB_USER"),
        "PASSWORD": os.getenv("PODCASTS_DB_PASSWORD"),
        "HOST": os.getenv("PODCASTS_DB_HOST"),
        "PORT": os.getenv("PODCASTS_DB_PORT", "5432"),
        "OPTIONS": {"sslmode": "require"},
    },
    "knowledge_graphdb": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("KG_DB_NAME"),
        "USER": os.getenv("KG_DB_USER"),
        "PASSWORD": os.getenv("KG_DB_PASSWORD"),
        "HOST": os.getenv("KG_DB_HOST"),
        "PORT": os.getenv("KG_DB_PORT", "5432"),
        "OPTIONS": {
            "sslmode": "require",
        },
    },
    "ai_assistant_db": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("AI_ASSISTANT_DB_NAME", "postgres"),
        "USER": os.getenv("AI_ASSISTANT_DB_USER", ""),
        "PASSWORD": os.getenv("AI_ASSISTANT_DB_PASSWORD", ""),
        "HOST": os.getenv("AI_ASSISTANT_DB_HOST", ""),
        "PORT": os.getenv("AI_ASSISTANT_DB_PORT", "5432"),
        "OPTIONS": {
            "sslmode": "require",
        },
    },
    "accountsdb": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("ACCOUNTS_DB_NAME", "postgres"),
        "USER": os.getenv("ACCOUNTS_DB_USER", ""),
        "PASSWORD": os.getenv("ACCOUNTS_DB_PASSWORD", ""),
        "HOST": os.getenv("ACCOUNTS_DB_HOST", ""),
        "PORT": os.getenv("ACCOUNTS_DB_PORT", "5432"),
        "OPTIONS": {"sslmode": "require"},
    },

}
# 資料庫連線重用：預設(CONN_MAX_AGE=0)每個請求都會重新連一次遠端 Supabase，實測每次連線要 0.8～1.5 秒
# (查詢本身只要 0.08～0.16 秒)，單集頁一次要打好幾支 API、每支又會用到 2～3 個資料庫，光連線就要好幾秒。
# 改成同一條連線保留 DB_CONN_MAX_AGE 秒給之後的請求重用；CONN_HEALTH_CHECKS 在重用前先確認連線還活著，
# 被 Supabase 中斷的連線會自動重連，不會讓請求失敗。注意：Supabase 的 session 模式連線池(port 5432)
# 每條保留中的連線都會佔一個名額，同時開的 worker/執行緒很多時要留意上限，必要時調小這個秒數。
# 本機 runserver 每個請求開一個新執行緒、連線跟著執行緒，重用不到，還會留下一堆沒關的連線佔掉連線池名額，
# 所以 runserver 預設維持 0(要測可以自己設 DB_CONN_MAX_AGE)；正式環境(gunicorn 等常駐 worker)才會真的重用。
import sys as _sys
_default_conn_age = "0" if "runserver" in _sys.argv else "60"
for _db in DATABASES.values():
    if _db.get("ENGINE", "").endswith("postgresql"):
        _db.setdefault("CONN_MAX_AGE", int(os.getenv("DB_CONN_MAX_AGE", _default_conn_age)))
        _db.setdefault("CONN_HEALTH_CHECKS", True)
# 資料庫 routing
DATABASE_ROUTERS = [
    "config.db_routers.EtfRouter",
    "config.db_routers.SummariesRouter",
    "config.db_routers.PodcastsRouter",
    "config.db_routers.KnowledgeGraphRouter",
    "config.db_routers.AiAssistantRouter",
    "config.db_routers.AccountsRouter",
]


# 大頭貼用 base64 塞進一般 JSON body（沒有設定檔案伺服器），預設 2.5MB 上限對
# ~2MB 原始圖檔轉出來的 base64 文字太緊，調高到 5MB 給一點餘裕。
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "apps.accounts.validators.LetterDigitPasswordValidator",
        "OPTIONS": {"min_length": 8},
    },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"