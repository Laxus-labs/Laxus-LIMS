"""
Django settings for the LIMS backend.

Configuration is environment-driven (12-factor) so the same image runs in
dev, CI, and production with only env vars changing. There is deliberately
no SQLite fallback: the schema depends on Postgres-specific features
(ltree, JSONB, partial unique indexes, row-level locking, triggers), so a
SQLite "convenience" mode would silently drop the guarantees this system
is built on.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
    "apps.accounts",
    "apps.audit",
    "apps.storage",
    "apps.customers",
    "apps.samples",
    "apps.testing",
    "apps.inventory",
    "apps.core",
    "apps.microbiology",
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
    # Binds the acting user to the DB session so audit triggers can record
    # *who* made a change. Must come after AuthenticationMiddleware.
    "apps.audit.middleware.AuditContextMiddleware",
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "lims_dev"),
        "USER": os.environ.get("DB_USER", os.environ.get("USER", "postgres")),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        # Reservation transactions are short and hot; reusing connections
        # avoids paying TCP + auth setup on every allocation.
        "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),
        "OPTIONS": {
            # Fail fast rather than pile up connections behind a stuck lock.
            "connect_timeout": 5,
        },
        "TEST": {"NAME": os.environ.get("TEST_DB_NAME", "lims_test")},
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
# Lab operations are local, but everything persisted is UTC. Timestamps in
# the audit trail must be unambiguous across sites and DST boundaries.
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.CursorPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "config.exceptions.lims_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "LIMS API",
    "DESCRIPTION": "Laboratory Information Management System",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

CORS_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ALLOWED_ORIGINS", "http://localhost:5173"
).split(",")

# --- Security --------------------------------------------------------------
# Applied whenever DEBUG is off, so the hardening is on by default in any
# real deployment rather than being a checklist someone has to remember.
# `manage.py check --deploy` is clean with DJANGO_DEBUG=false.
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    # Behind a load balancer terminating TLS, this is how Django learns the
    # original request was HTTPS. Only trust it when such a proxy exists.
    if env_bool("BEHIND_TLS_PROXY", False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    if SECRET_KEY == "dev-only-insecure-key-change-me":
        raise RuntimeError(
            "DJANGO_SECRET_KEY must be set to a real value when DEBUG is off."
        )

# --- Domain configuration -------------------------------------------------

# How long an unconsumed stock reservation is held before a sweeper may
# reclaim it. Prevents a crashed worker from stranding inventory forever.
RESERVATION_TTL_SECONDS = int(os.environ.get("RESERVATION_TTL_SECONDS", "900"))
