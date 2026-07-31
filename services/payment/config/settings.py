"""Django settings for the Payment service.

Config is env-driven (12-factor): DB URL, signing keys, log level all come from
the environment (from `.env` in local dev, a secrets manager in prod). Nothing
service-specific is hardcoded.
"""

from __future__ import annotations

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

from ledgerstream_shared.config import get_bool, get_env, require_env

# --- Paths -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent      # services/payment
REPO_ROOT = BASE_DIR.parent.parent                     # repo root (holds .env)

# Load the repo-root .env for native local dev. In containers/prod the
# environment is already populated, and python-dotenv simply finds nothing to do.
load_dotenv(REPO_ROOT / ".env")

# --- Core Django -------------------------------------------------------------
SECRET_KEY = get_env("DJANGO_SECRET_KEY", "dev-insecure-change-me-in-prod")
DEBUG = get_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = get_env("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    # Local apps
    "core",
    "tenants",
    "payments",
    "outbox",
]

MIDDLEWARE = [
    # Correlation-id first, so every downstream log/span carries it.
    "core.middleware.CorrelationIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

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

# --- Database (per-service Postgres; Neon in cloud mode) ---------------------
# conn_max_age keeps connections open (a simple form of pooling) instead of
# opening a new TCP+TLS connection per request — important against a remote DB.
DATABASES = {
    "default": dj_database_url.parse(
        require_env("PAYMENT_DATABASE_URL"),
        conn_max_age=int(get_env("DB_CONN_MAX_AGE", "60")),
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Django REST Framework + JWT --------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "EXCEPTION_HANDLER": "core.exceptions.exception_handler",
}

from datetime import timedelta  # noqa: E402  (kept next to its use)

SIMPLE_JWT = {
    # One signing key for the whole platform (service-to-service trust comes
    # later); loaded from env so it's never in code.
    "SIGNING_KEY": require_env("JWT_SIGNING_KEY"),
    "ALGORITHM": "HS256",
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(get_env("JWT_ACCESS_MINUTES", "30"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(get_env("JWT_REFRESH_DAYS", "1"))),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# --- Observability -----------------------------------------------------------
# Hand logging to the shared library's JSON formatter instead of Django's config.
LOGGING_CONFIG = None
LOG_LEVEL = get_env("LOG_LEVEL", "INFO")
# Actual logging/tracing setup happens in core.apps.CoreConfig.ready() so it runs
# exactly once per process (including worker processes), after apps are loaded.

SERVICE_NAME = "payment-service"
