"""
Django settings for the Distribution Management SaaS.

Everything environment-specific comes from environment variables (or backend/.env).
"""
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, []),
    CELERY_TASK_ALWAYS_EAGER=(bool, True),
    USE_S3=(bool, False),
    PUBLIC_BASE_URL=(str, "http://localhost:8000"),
    FRONTEND_BASE_URL=(str, "http://localhost:5173"),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-change-me")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL")
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "django_filters",
    "corsheaders",
    "django_celery_beat",
    # project apps
    "apps.core",
    "apps.accounts",
    "apps.platform",
    "apps.catalog",
    "apps.partners",
    "apps.inventory",
    "apps.purchasing",
    "apps.sales",
    "apps.delivery",
    "apps.accounting",
    "apps.reports",
    "apps.integrations",
    "apps.fbr",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.TenantContextMiddleware",
    "apps.core.middleware.RequestMetaMiddleware",
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
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {"default": env.db("DATABASE_URL", default="postgres://dms_saas:dms_saas@127.0.0.1:5432/dms_saas")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DATABASES["default"]["CONN_MAX_AGE"] = 60

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://127.0.0.1:6379/1"),
    }
}

AUTH_USER_MODEL = "accounts.User"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
# Day/month/year wherever Django renders a date itself — see config/formats/en/formats.py.
FORMAT_MODULE_PATH = ["config.formats"]
# The business, the host and PostgreSQL all run on Pakistan time, and Django was the one piece left on UTC.
# That mismatch was not cosmetic: `timezone.localdate()` gave the *UTC* date, so an order taken at one in the
# morning in Lahore was dated the previous day, and a rule written as "until midnight of the day of sale"
# closed five hours before the rep typed the order. Datetimes are still stored in UTC (`USE_TZ`); this changes
# only what "today" and "local time" mean, which is what every document date is derived from.
TIME_ZONE = "Asia/Karachi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

if env("USE_S3"):
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "access_key": env("S3_ACCESS_KEY"),
                "secret_key": env("S3_SECRET_KEY"),
                "bucket_name": env("S3_BUCKET"),
                "endpoint_url": env("S3_ENDPOINT_URL", default=None),
                "region_name": env("S3_REGION", default="us-east-1"),
                "default_acl": "private",
                "querystring_auth": True,
                "querystring_expire": 3600,
            },
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.TenantJWTAuthentication",
        "apps.integrations.authentication.ApiKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.problem_exception_handler",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    # Several endpoints read `?format=pdf|csv|xlsx` themselves and return the file (report exports, the
    # customer statement). DRF also claims that query parameter to pick a renderer, and with only
    # JSONRenderer installed it answered every one of those calls with 404 before the view ever ran — the
    # Statement PDF button and all three report export buttons were dead. Nothing here negotiates by URL.
    "URL_FORMAT_OVERRIDE": None,
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "auth": "20/min",
        "otp": "5/min",
        "ext_api": "60/min",
        "agent_mail": "120/min",
    },
    "COERCE_DECIMAL_TO_STRING": True,
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_OBTAIN_SERIALIZER": "apps.accounts.serializers.LoginSerializer",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Distribution Management SaaS API",
    "DESCRIPTION": "Multi-tenant distribution management API (web, mobile, rep app and external systems).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "SWAGGER_UI_SETTINGS": {"persistAuthorization": True},
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "jwtAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
            "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            "tenantHeader": {"type": "apiKey", "in": "header", "name": "X-Tenant-ID"},
        }
    },
    "SECURITY": [{"jwtAuth": [], "tenantHeader": []}, {"apiKeyAuth": []}],
}

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "x-tenant-id",
    "x-api-key",
    "idempotency-key",
    "x-requested-with",
]
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/1")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = env("CELERY_TASK_ALWAYS_EAGER")
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TIMEZONE = "Asia/Karachi"      # so a nightly task fires at night here, not at five in the morning
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_BEAT_SCHEDULE = {
    "subscription-status-daily": {"task": "apps.platform.tasks.check_subscription_statuses", "schedule": 60 * 60 * 24},
    "renewal-reminders-daily": {"task": "apps.platform.tasks.send_renewal_reminders", "schedule": 60 * 60 * 24},
    "stock-cache-recompute-nightly": {"task": "apps.inventory.tasks.recompute_stock_levels", "schedule": 60 * 60 * 24},
    "expire-reservations": {"task": "apps.sales.tasks.expire_reservations", "schedule": 60 * 15},
    "nightly-reconciliation": {"task": "apps.accounting.tasks.nightly_reconciliation", "schedule": 60 * 60 * 24},
    "inventory-alerts": {"task": "apps.inventory.tasks.compute_inventory_alerts", "schedule": 60 * 60 * 6},
    "retry-webhooks": {"task": "apps.integrations.tasks.retry_failed_webhooks", "schedule": 60 * 5},
    "purge-location-history-daily": {"task": "apps.partners.tasks.purge_location_history", "schedule": 60 * 60 * 24},
    "sweep-shifts": {"task": "apps.partners.tasks.sweep_shifts", "schedule": 60 * 5},
}

# ---------------------------------------------------------------------------
# Email / SMS / Push providers
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=1025)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@dms.local")

SMS_PROVIDER = env("SMS_PROVIDER", default="console")  # console | http
SMS_HTTP_URL = env("SMS_HTTP_URL", default="")
SMS_HTTP_TOKEN = env("SMS_HTTP_TOKEN", default="")
FCM_CREDENTIALS_FILE = env("FCM_CREDENTIALS_FILE", default="")

# ---------------------------------------------------------------------------
# Application settings
# ---------------------------------------------------------------------------
DMS = {
    "OTP_TTL_MINUTES": 10,
    "LOGIN_LOCKOUT_ATTEMPTS": 10,
    "LOGIN_LOCKOUT_MINUTES": 15,
    "IMPERSONATION_MINUTES": 60,
    # A refresh token that was rotated this many seconds ago is still honoured once (two tabs refreshing at the
    # same moment is a race, not token theft) — without it one of them would be logged out.
    "REFRESH_GRACE_SECONDS": 60,
    "DEFAULT_TRIAL_DAYS": 14,
    "DEFAULT_PLAN_CODE": "growth",
    "RESERVATION_TTL_HOURS": 48,
    "REP_UNSETTLED_ALERT_DAYS": 3,
    "WEBHOOK_MAX_ATTEMPTS": 6,
    "QR_LOGIN_TTL_SECONDS": 300,  # web → phone "scan to log in" codes: single use, 5 minutes
}

FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024  # non-file form data; APK uploads stream to disk (FILE_UPLOAD_MAX_MEMORY_SIZE)
ALLOWED_UPLOAD_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "pdf", "csv", "xlsx", "xls", "txt"}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"format": '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
