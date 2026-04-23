import os
from pathlib import Path

from dotenv import load_dotenv
from apps.utils.tasking import build_task_routes

BASE_DIR = Path(__file__).resolve().parent.parent
# urban_hub_app/.env — Django & scripts sur l’hôte (localhost). Celery : surcharges dans docker-compose.
_env_file = BASE_DIR / '.env'
if _env_file.is_file():
    try:
        load_dotenv(_env_file, encoding='utf-8')
    except UnicodeDecodeError:
        load_dotenv(_env_file, encoding='cp1252')
else:
    load_dotenv(_env_file)
# Après lecture du .env : défaut si la clé n’y figure pas (surcharge possible via PGCLIENTENCODING=... dans .env).
os.environ.setdefault('PGCLIENTENCODING', 'UTF8')

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key')
DEBUG = os.environ.get('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'corsheaders',
    'django_celery_beat',
    'django_celery_results',
    # Apps UrbanHub
    'apps.dashboard',
    'apps.meteo',
    'apps.mobility',
    'apps.pollution',
    'apps.analysis',
    'apps.serving',
    'apps.urban_guide',
    'apps.utils',
]

# ─── Dashboard Gold (Postgres serving + MinIO Parquet) ────────────
DASHBOARD_GOLD_MOBILITY = os.environ.get('DASHBOARD_GOLD_MOBILITY', 'False') == 'True'
DASHBOARD_GOLD_AIR = os.environ.get('DASHBOARD_GOLD_AIR', 'False') == 'True'
DASHBOARD_GOLD_CLIMATE = os.environ.get('DASHBOARD_GOLD_CLIMATE', 'False') == 'True'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'urbanhub.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'urbanhub.wsgi.application'

# ─── Database (PostgreSQL + TimescaleDB) ──────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'urbanhub'),
        'USER': os.environ.get('POSTGRES_USER', 'urbanhub'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
        'HOST': os.environ.get('POSTGRES_HOST', '127.0.0.1'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        'OPTIONS': {
            'client_encoding': 'UTF8',
        },
    }
}

# ─── Celery ───────────────────────────────────────────────────────
CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = 'django-db'
CELERY_CACHE_BACKEND = 'django-cache'
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
CELERY_TIMEZONE = 'Europe/Paris'
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_DEFAULT_QUEUE = 'default'
CELERY_TASK_ROUTES = build_task_routes()
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_CREATE_MISSING_QUEUES = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# ─── MinIO / S3 Storage ───────────────────────────────────────────
AWS_ACCESS_KEY_ID = os.environ.get('MINIO_ROOT_USER', 'minioadmin')
AWS_SECRET_ACCESS_KEY = os.environ.get('MINIO_ROOT_PASSWORD', 'minioadmin123')
AWS_STORAGE_BUCKET_NAME = os.environ.get('MINIO_BUCKET_RAW', 'urbanhub-raw')
_minio_ep = os.environ.get('MINIO_ENDPOINT', 'minio:9000')
AWS_S3_ENDPOINT_URL = f'http://{_minio_ep}'
AWS_S3_ADDRESSING_STYLE = 'path'
AWS_DEFAULT_ACL = None

MINIO_BUCKETS = {
    'raw': os.environ.get('MINIO_BUCKET_RAW', 'urbanhub-raw'),
    'processed': os.environ.get(
        'MINIO_BUCKET_PROCESSED', 'urbanhub-processed'
    ),
    'analytics': os.environ.get(
        'MINIO_BUCKET_ANALYTICS', 'urbanhub-analytics'
    ),
}

# ─── Cache (Redis) ────────────────────────────────────────────────
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': os.environ.get('REDIS_URL', 'redis://redis:6379/0'),
        'KEY_PREFIX': 'urbanhub',
        'TIMEOUT': 300,
    }
}

# ─── Logging ──────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {asctime} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'celery': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# ─── REST Framework ───────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_PAGINATION_CLASS': (
        'rest_framework.pagination.PageNumberPagination'
    ),
    'PAGE_SIZE': 100,
}

CORS_ALLOW_ALL_ORIGINS = DEBUG

# ─── Static / Media ───────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Europe/Paris'
USE_I18N = True
USE_TZ = True
