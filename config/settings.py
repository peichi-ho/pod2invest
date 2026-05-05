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
DEBUG = os.getenv('DJANGO_DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

# Vertex AI / Gemini settings
VERTEX_PROJECT_ID = os.getenv("VERTEX_PROJECT_ID", "gen-lang-client-0357610042")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

# 讓 Google auth 函式庫自動找到服務帳戶金鑰
_gac = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
if _gac and not os.path.isabs(_gac):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(BASE_DIR / _gac)

# 股票資料快取秒數（避免 yfinance 太慢）
STOCK_CACHE_TTL_SECONDS = int(os.getenv("STOCK_CACHE_TTL_SECONDS", "60"))

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'apps.glossary.apps.GlossaryConfig',
    'apps.summaries.apps.SummariesConfig',
    'apps.etf.apps.EtfConfig',
    'apps.podcasts.apps.PodcastsConfig',
    'apps.mindmap.apps.MindmapConfig',
    'apps.ai_assistant.apps.AiAssistantConfig',
    'apps.knowledge_graph.apps.KnowledgeGraphConfig',
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

}
# 資料庫 routing
DATABASE_ROUTERS = [
    "config.db_routers.EtfRouter",
    "config.db_routers.SummariesRouter",
    "config.db_routers.PodcastsRouter",
    "config.db_routers.KnowledgeGraphRouter",
    "config.db_routers.AiAssistantRouter",
]


AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"
    },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
