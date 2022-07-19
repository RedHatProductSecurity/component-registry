import os
from distutils.util import strtobool
from pathlib import Path

# noinspection PyPep8Naming
from corgi import __version__ as CORGI_VERSION

# Build paths inside the project like this: BASE_DIR / "subdir".
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Added
CA_CERT = os.getenv("REQUESTS_CA_BUNDLE")

DEBUG = False

# Mail these people on uncaught exceptions that result in 500 errors
ADMINS = [tuple(name_and_email.split(";")) for name_and_email in os.getenv("CORGI_ADMINS", "").split(",")]

CORGI_DOMAIN = os.getenv("CORGI_DOMAIN")
if CORGI_DOMAIN:
    CSRF_COOKIE_DOMAIN = CORGI_DOMAIN
    LANGUAGE_COOKIE_DOMAIN = CORGI_DOMAIN
    SESSION_COOKIE_DOMAIN = CORGI_DOMAIN

EMAIL_HOST = os.getenv("CORGI_EMAIL_HOST", "localhost")
EMAIL_PORT = 1025 if EMAIL_HOST == "localhost" else 25
EMAIL_USE_TLS = False if EMAIL_HOST == "localhost" else True
SERVER_EMAIL = os.getenv("CORGI_SERVER_EMAIL", "root@localhost")
DEFAULT_FROM_EMAIL = SERVER_EMAIL

APPEND_SLASH = False  # Default: True
# If True, and request URL doesn't match any patterns in URLconf
# and request URL doesn’t end in slash
# HTTP redirect is issued to same URL with slash appended
# Note that the redirect may cause any data submitted in a POST request to be lost
# With False, our URLconf controls. "api/path/" won't match, should give a 404
# Only "api/path" should succeed

# Cookie settings
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_NAME = "corgi_csrf_token"
CSRF_COOKIE_SAMESITE = "Strict"

LANGUAGE_COOKIE_HTTPONLY = True
LANGUAGE_COOKIE_SECURE = True
LANGUAGE_COOKIE_NAME = "corgi_language"
LANGUAGE_COOKIE_SAMESITE = "Strict"

SESSION_COOKIE_HTTPONLY = True  # Django default
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_NAME = "corgi_session_id"
SESSION_COOKIE_SAMESITE = "Strict"

# Traffic from OCP router to Django is via HTTP. Because the TLS route is edge terminated,
# HTTP features that need a secure connection use the below Django setting in stage / prod
# to tell Django the connection is secure. Otherwise Django "sees" that the connection
# from the client is via HTTP, and does not send HSTS headers, for example
# The OpenShift router / HAProxy instance MUST force setting these headers,
# overwriting them if already present
# Otherwise bad clients can trick Django into thinking the connection is secure when it isn't
# See https://docs.djangoproject.com/en/4.0/ref/settings/#secure-proxy-ssl-header
# SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Security headers
SECURE_HSTS_SECONDS = 15768000  # 182.5 days, i.e. 6 months
SECURE_HSTS_INCLUDE_SUBDOMAINS = True  # Adds includeSubDomains to Strict-Transport-Security header
# SECURE_SSL_REDIRECT = True  # This causes an infinite redirect loop due to edge termination

SECURE_CONTENT_TYPE_NOSNIFF = True  # Header: X-Content-Type-Options: nosniff
SECURE_BROWSER_XSS_FILTER = True  # Header: X-XSS-Protection: 1; mode=block
X_FRAME_OPTIONS = "DENY"  # Header: X-Frame-Options DENY

# Content Security Policy
CSP_STYLE_SRC = (
    "'self'",
    "'unsafe-inline'",
    "https://cdnjs.cloudflare.com",
    "https://cdn.jsdelivr.net",
)
CSP_FONT_SRC = (
    "'self'",
    "https://cdnjs.cloudflare.com",
)
CSP_SCRIPT_SRC = (
    "'self'",
    "'unsafe-inline'",
    "https://cdnjs.cloudflare.com",
    "https://cdn.jsdelivr.net",
)
CSP_IMG_SRC = (
    "'self'",
    "data:",
    "https://cdn.jsdelivr.net",
)
CSP_DEFAULT_SRC = (
    "'self'",
    "data:",
)

# RFC 5322 datetime format used in web UIs, nicer to read than ISO8601
# Ex: 'Thu, 21 Dec 2000 16:01:07 +0200'
DATETIME_FORMAT = "r"

# Application definition
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django_celery_results",
    "django_celery_beat",
    "drf_spectacular",
    "mptt",
    "rest_framework",
    "django_filters",
    "corgi.api",
    "corgi.core",
    "corgi.collectors",
    "corgi.monitor",
    "corgi.tasks",
    "corgi.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "csp.middleware.CSPMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [str(BASE_DIR / "corgi/web/templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "[%(asctime)s] [%(name)s:%(lineno)d] %(levelname)s: %(message)s",
            "datefmt": "%d/%b/%Y:%H:%M:%S %z",  # Matches the one used by gunicorn
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING"},
        "corgi": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
    },
}

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("CORGI_DB_NAME", "corgi-db"),
        "USER": os.getenv("CORGI_DB_USER", "corgi-db-user"),
        "PASSWORD": os.getenv("CORGI_DB_PASSWORD", "test"),
        "HOST": os.getenv("CORGI_DB_HOST", "localhost"),
        "PORT": os.getenv("CORGI_DB_PORT", "5432"),
    }
}

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_L10N = False
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATIC_ROOT = str(BASE_DIR / "staticfiles")
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Celery config
CELERY_BROKER_URL = os.getenv("CORGI_REDIS_URL", "redis://redis:6379")

CELERY_RESULT_BACKEND = "django-db"
# Retry tasks due to Postgres failures instead of immediately re-raising exceptions
# See https://docs.celeryproject.org/en/stable/userguide/configuration.html for details
# See also a django-celery-results decorator for individual tasks:
# https://django-celery-results.readthedocs.io/en/latest/reference/django_celery_results.managers.html
CELERY_RESULT_BACKEND_ALWAYS_RETRY = True
CELERY_RESULT_BACKEND_MAX_RETRIES = 2
CELERY_DEFAULT_RATE_LIMIT = "8/m"
CELERYD_SOFT_TIME_LIMIT = 300

CELERY_WORKER_CONCURRENCY = 1  # defaults to CPU core count, which breaks in OpenShift

# Disable task prefetching, which caused connection timeouts and other odd task failures in SDEngine
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# Store the return values of each task in the TaskResult.result attribute; can be used for
# informational logging.
CELERY_TASK_IGNORE_RESULT = False

# Track the start time of each task by creating its TaskResult as soon as it enters the STARTED
# state. This allows us to measure task execution time of each task by `date_done - date_created`.
CELERY_TASK_TRACK_STARTED = True

# Do not acknowledge task until completion
# Otherwise tasks may be lost when nodes evict Celery worker pods
CELERY_TASK_ACKS_LATE = True

# Disable task result expiration:
# https://docs.celeryproject.org/en/latest/userguide/configuration.html#std-setting-result_expires
# By default, this job is enabled and runs daily at 4am. Disable to keep UMB-triggered task results
CELERY_RESULT_EXPIRES = None

# Mail these people once a day if any Celery task failed in the past 24 hours
# Technically, "twice a day" - you'll get an email from stage and prod
FAILED_CELERY_TASK_SUBSCRIBERS = os.getenv("CORGI_FAILED_CELERY_TASK_SUBSCRIBERS", "").split(",")

CELERY_TASK_ROUTES = (
    [
        ("corgi.tasks.*.slow_*", {"queue": "slow"}),  # Any module's slow_* tasks go to 'slow' queue
        ("*", {"queue": "fast"}),  # default other tasks go to 'fast'
    ],
)


# Django REST Framework
# https://www.django-rest-framework.org/
REST_FRAMEWORK = {
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # "rest_framework.authentication.BasicAuthentication",
        # "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        # "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 10,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "corgi.api.exception_handlers.exception_handler",
}


# UMB -- Unified Message Bus
UMB_CERT = os.getenv("CORGI_UMB_CERT")
UMB_KEY = os.getenv("CORGI_UMB_KEY")

UMB_CONSUMER_ID = os.getenv("CORGI_UMB_CONSUMER_ID")
UMB_SUBSCRIPTION_ID = os.getenv("CORGI_UMB_SUBSCRIPTION_ID")
UMB_CONSUMER = f"{UMB_CONSUMER_ID}.{UMB_SUBSCRIPTION_ID}"

UMB_BROKER_URL = os.getenv("CORGI_UMB_BROKER_URL")

# Set to False to turn off the brew umb listener.
# True values are y, yes, t, true, on and 1; false values are n, no, f, false, off and 0
# https://docs.python.org/3/distutils/apiref.html#distutils.util.strtobool
UMB_BREW_MONITOR_ENABLED = strtobool(os.getenv("CORGI_UMB_BREW_MONITOR_ENABLED", "true"))

# Brew
BREW_URL = os.getenv("CORGI_BREW_URL")
BREW_DOWNLOAD_ROOT_URL = os.getenv("CORGI_BREW_DOWNLOAD_ROOT_URL")

# RHEL Compose
RHEL_COMPOSE_BASE_URL = os.getenv("CORGI_TEST_DOWNLOAD_URL")

# ProdSec Dashboard
PRODSEC_DASHBOARD_URL = os.getenv("CORGI_PRODSEC_DASHBOARD_URL")

# Errata Tool
ERRATA_TOOL_URL = os.getenv("CORGI_ERRATA_TOOL_URL")

# Settings for the drf-spectacular package
SPECTACULAR_SETTINGS = {
    "TITLE": "Component Registry API",
    "DESCRIPTION": "REST API auto-generated docs for Component Registry",
    "VERSION": CORGI_VERSION,
    "SWAGGER_UI_SETTINGS": {"supportedSubmitMethods": []},
}

# URL where lifecycle collector fetches application streams from
APP_STREAMS_LIFE_CYCLE_URL = os.getenv("CORGI_APP_STREAMS_LIFE_CYCLE_URL", "")

# Manifest hints url
MANIFEST_HINTS_URL = os.getenv("CORGI_MANIFEST_HINTS_URL")

DISTGIT_DIR = os.getenv("CORGI_DISTGIT_DIR", "/opt/distgit")
LOOKASIDE_CACHE_BASE_URL = f"https://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}/repo"
LOOKASIDE_DIR = os.getenv("CORGI_LOOKASIDE_DIR", "/opt/lookaside")
SCA_SCRATCH_DIR = os.getenv("CORGI_SCA_SCATCH_DIR", "/tmp")
