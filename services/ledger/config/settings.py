"""Django settings for the Ledger service.

Mirrors the Payment service's env-driven config, but points at the Ledger's own
Postgres (`LEDGER_DATABASE_URL`). Auth is **stateless**: the Ledger validates JWTs
signed with the shared `JWT_SIGNING_KEY` (service-to-service trust) and reads the
tenant from the token WITHOUT a local user table — it never issued the token and
doesn't own the users.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

from ledgerstream_shared.config import get_bool, get_env, require_env

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent.parent
load_dotenv(REPO_ROOT / ".env")

SECRET_KEY = get_env("DJANGO_SECRET_KEY", "dev-insecure-change-me-in-prod")
DEBUG = get_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = get_env("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "core",
    "ledger",
    "consumer",   # holds the consume_payments management command (no models)
]

MIDDLEWARE = [
    "core.middleware.CorrelationIdMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.parse(
        require_env("LEDGER_DATABASE_URL"),
        conn_max_age=int(get_env("DB_CONN_MAX_AGE", "60")),
        conn_health_checks=True,
    )
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    # Custom auth: validate the JWT signature/expiry, but DON'T look up a user in
    # the DB (the Ledger owns no users — see core/authentication.py).
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "core.authentication.StatelessJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
}

SIMPLE_JWT = {
    # THE trust anchor: the SAME signing key as the Payment service. A token minted
    # by Payment's login verifies here → service-to-service trust with no shared
    # user store. HS256 is symmetric (same key signs + verifies); prod would use
    # RS256 so verifiers can check but not mint tokens (see core/authentication.py).
    "SIGNING_KEY": require_env("JWT_SIGNING_KEY"),
    "ALGORITHM": "HS256",
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(get_env("JWT_ACCESS_MINUTES", "30"))),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

LOGGING_CONFIG = None
LOG_LEVEL = get_env("LOG_LEVEL", "INFO")
SERVICE_NAME = "ledger-service"
