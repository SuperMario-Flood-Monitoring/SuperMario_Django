import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

APP_ENV = os.getenv("APP_ENV", os.getenv("DJANGO_ENV", "local")).lower()
IS_PRODUCTION = APP_ENV in {"prod", "production"}


def csv_env(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-development-only")
SUPERMARIO_JWT_SECRET_KEY = os.getenv("SUPERMARIO_JWT_SECRET_KEY", SECRET_KEY)
DEBUG = os.getenv("DJANGO_DEBUG", "false" if IS_PRODUCTION else "true").lower() == "true"
SUPERMARIO_REFRESH_COOKIE_SAMESITE = os.getenv("SUPERMARIO_REFRESH_COOKIE_SAMESITE", "Lax")
SUPERMARIO_REFRESH_COOKIE_SECURE = os.getenv(
    "SUPERMARIO_REFRESH_COOKIE_SECURE",
    "true" if IS_PRODUCTION else "false",
).lower() == "true"

DEFAULT_ALLOWED_HOSTS = (
    "supermario.o-r.kr,59.9.136.144,192.168.0.101,localhost,127.0.0.1"
    if IS_PRODUCTION
    else "localhost,127.0.0.1"
)
ALLOWED_HOSTS = csv_env("DJANGO_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS)

DEFAULT_PUBLIC_BASE_URL = "https://supermario.o-r.kr" if IS_PRODUCTION else "http://127.0.0.1:5173"
SUPERMARIO_PUBLIC_BASE_URL = os.getenv("SUPERMARIO_PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL).rstrip("/")
SUPERMARIO_API_BASE_URL = os.getenv(
    "SUPERMARIO_API_BASE_URL",
    f"{SUPERMARIO_PUBLIC_BASE_URL}/api",
).rstrip("/")
SUPERMARIO_LLM_BASE_URL = os.getenv(
    "SUPERMARIO_LLM_BASE_URL",
    "https://supermario.o-r.kr/llm" if IS_PRODUCTION else "http://127.0.0.1:8001/llm",
).rstrip("/")
SUPERMARIO_LLM_ANALYZE_URL = os.getenv(
    "SUPERMARIO_LLM_ANALYZE_URL",
    f"{SUPERMARIO_LLM_BASE_URL}/analyze",
)
SUPERMARIO_LLM_MAINTENANCE_LOG_URL = os.getenv(
    "SUPERMARIO_LLM_MAINTENANCE_LOG_URL",
    f"{SUPERMARIO_LLM_BASE_URL}/maintenance/log/",
)
SUPERMARIO_LLM_MAINTENANCE_LOG_TIMEOUT_SECONDS = float(
    os.getenv("SUPERMARIO_LLM_MAINTENANCE_LOG_TIMEOUT_SECONDS", "10")
)
SUPERMARIO_FORECAST_MINUTES = int(os.getenv("SUPERMARIO_FORECAST_MINUTES", "10"))
SUPERMARIO_FORECAST_WINDOW_SECONDS = int(os.getenv("SUPERMARIO_FORECAST_WINDOW_SECONDS", "120"))
SUPERMARIO_FORECAST_BUFFER_SECONDS = int(os.getenv("SUPERMARIO_FORECAST_BUFFER_SECONDS", "900"))

INSTALLED_APPS = [
    "daphne",
    "corsheaders",
    "apps.auth.apps.AuthConfig",
    "apps.facilities",
    "apps.monitoring.apps.MonitoringConfig",
    "apps.notification.apps.NotificationConfig",
    "apps.scenarios",
    "apps.simulation",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "apps.auth.middleware.ApiJwtAuthenticationMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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
ASGI_APPLICATION = "config.asgi.application"

DATABASE_ENGINE = os.getenv("DATABASE_ENGINE", "sqlite").lower()

if DATABASE_ENGINE == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "supermario"),
            "USER": os.getenv("POSTGRES_USER", "supermario"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
            "HOST": os.getenv("POSTGRES_HOST", "postgres"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("SQLITE_PATH", BASE_DIR / "db.sqlite3"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = False

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DEFAULT_CORS_ALLOWED_ORIGINS = (
    "https://supermario.o-r.kr,http://supermario.o-r.kr,http://59.9.136.144,http://192.168.0.101"
    if IS_PRODUCTION
    else "http://localhost:5173,http://127.0.0.1:5173"
)
CORS_ALLOWED_ORIGINS = csv_env("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ALLOWED_ORIGINS)
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}
