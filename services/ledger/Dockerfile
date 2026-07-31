# Ledger service image. Build from the REPO ROOT (shared lib is in the context):
#   docker build -f services/ledger/Dockerfile -t ledgerstream/ledger .
#
# ONE image, TWO run modes (different command per Kubernetes Deployment):
#   web      → gunicorn (the read API)          — the default CMD below
#   consumer → python manage.py consume_payments — the Kafka worker
# The consumer does NOT need the .avsc files — it fetches schemas from the
# registry by id — so we don't COPY schemas/ here.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY libs/shared /app/libs/shared
RUN pip install /app/libs/shared

COPY services/ledger/requirements.txt /app/services/ledger/requirements.txt
RUN pip install -r /app/services/ledger/requirements.txt

COPY services/ledger /app/services/ledger
WORKDIR /app/services/ledger

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Default = the web/read API. The consumer Deployment overrides CMD with:
#   ["python", "manage.py", "consume_payments"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "30", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-"]
