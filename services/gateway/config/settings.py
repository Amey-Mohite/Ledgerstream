"""Django settings for the Gateway (API gateway / BFF).

The gateway is **stateless**: it owns no database. It authenticates callers at the
edge (stateless JWT, shared signing key), then reverse-proxies to the Payment and
Ledger services over HTTP. Its only backing store is **Redis** (rate-limit counters
+ cache), wired in later chunks. So there is NO `DATABASES` config here — accessing
the ORM would be a bug, and `DATABASES = {}` makes that explicit.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

from ledgerstream_shared.config import get_bool, get_env, require_env

BASE_DIR = Path(__file__).resolve().parent.parent      # services/gateway
REPO_ROOT = BASE_DIR.parent.parent                     # repo root (holds .env)
load_dotenv(REPO_ROOT / ".env")

SECRET_KEY = get_env("DJANGO_SECRET_KEY", "dev-insecure-change-me-in-prod")
DEBUG = get_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = get_env("DJANGO_ALLOWED_HOSTS", "*").split(",")

# contenttypes + auth are kept because DRF/SimpleJWT import them, but nothing here
# touches their tables (StatelessJWT builds the principal from token claims, and
# DATABASES is empty). No "gateway" models exist — the app is pure request routing.
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "core",
    "gateway",
]

MIDDLEWARE = [
    "core.middleware.CorrelationIdMiddleware",   # first → every log/span carries the id
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# Stateless gateway → NO database. The ORM must never be used.
DATABASES: dict = {}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF: edge authentication -------------------------------------------------
REST_FRAMEWORK = {
    # Validate the JWT signature + expiry with the shared key; no user lookup
    # (the gateway owns no users — see core/authentication.py). This is the EDGE
    # auth: reject unauthenticated traffic before it ever reaches a backend.
    "DEFAULT_AUTHENTICATION_CLASSES": ("core.authentication.StatelessJWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
}

SIMPLE_JWT = {
    # SAME signing key as Payment (which mints tokens) and Ledger → service-to-
    # service trust with no shared user store. HS256 symmetric; prod → RS256.
    "SIGNING_KEY": require_env("JWT_SIGNING_KEY"),
    "ALGORITHM": "HS256",
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(get_env("JWT_ACCESS_MINUTES", "30"))),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# --- Downstream services (reverse-proxy targets) -----------------------------
# In prod these are service DNS names; locally, the native dev servers.
PAYMENT_BASE_URL = get_env("PAYMENT_BASE_URL", "http://localhost:8000")
LEDGER_BASE_URL = get_env("LEDGER_BASE_URL", "http://localhost:8021")
DOWNSTREAM_TIMEOUT = float(get_env("DOWNSTREAM_TIMEOUT", "5.0"))   # seconds

# Redis backs rate limiting + cache.
REDIS_URL = get_env("REDIS_URL", "redis://localhost:6379/0")

# --- Rate limiting (token bucket, per tenant / per IP) ------------------------
# capacity = max burst; refill_per_sec = sustained rate. 60 burst + 1/s ≈ 60/min.
RATE_LIMIT_CAPACITY = int(get_env("RATE_LIMIT_CAPACITY", "60"))
RATE_LIMIT_REFILL_PER_SEC = float(get_env("RATE_LIMIT_REFILL_PER_SEC", "1.0"))

# --- Cache-aside (bounded staleness) -----------------------------------------
CACHE_TTL = int(get_env("CACHE_TTL", "30"))          # seconds a cached read may be stale

# --- Circuit breaker (per downstream service) --------------------------------
BREAKER_THRESHOLD = int(get_env("BREAKER_THRESHOLD", "5"))    # consecutive fails → OPEN
BREAKER_COOLDOWN = float(get_env("BREAKER_COOLDOWN", "10.0")) # seconds OPEN before a probe

# --- Observability ------------------------------------------------------------
LOGGING_CONFIG = None
LOG_LEVEL = get_env("LOG_LEVEL", "INFO")
SERVICE_NAME = "gateway-service"
