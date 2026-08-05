# Gateway service image (stateless API gateway / BFF).
#
# BUILD FROM THE REPO ROOT so the shared library is in the build context:
#   docker build -f services/gateway/Dockerfile -t ledgerstream/gateway .
#
# No Postgres, no Avro schemas — the gateway only proxies + talks to Redis.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1. Shared library first — changes rarely, caches well.
COPY libs/shared /app/libs/shared
RUN pip install /app/libs/shared

# 2. Service dependencies (separate cached layer).
COPY services/gateway/requirements.txt /app/services/gateway/requirements.txt
RUN pip install -r /app/services/gateway/requirements.txt

# 3. Service code last (changes most often).
COPY services/gateway /app/services/gateway
WORKDIR /app/services/gateway

# Run as a non-root user (least privilege).
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8010

# gunicorn handles SIGTERM gracefully (drains in-flight requests within
# --graceful-timeout before exiting).
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8010", \
     "--workers", "3", \
     "--timeout", "30", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-"]
