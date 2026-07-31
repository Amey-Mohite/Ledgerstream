# Payment service image.
#
# BUILD FROM THE REPO ROOT so the shared library is in the build context:
#   docker build -f services/payment/Dockerfile -t ledgerstream/payment .
#
# The shared lib is COPYed + installed here — a copy is baked into this image
# (see docs/phase0.md "How the shared library gets deployed").

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1. Shared library first — it changes rarely, so this layer caches well.
COPY libs/shared /app/libs/shared
RUN pip install /app/libs/shared

# 2. Service dependencies (separate layer, cached unless requirements change).
COPY services/payment/requirements.txt /app/services/payment/requirements.txt
RUN pip install -r /app/services/payment/requirements.txt

# 3. Shared Avro schemas — the outbox relay loads these to serialize events.
#    (Placed so the shared kafka helper's upward search finds /app/schemas/avro.)
COPY schemas /app/schemas

# 4. Service code last (changes most often).
COPY services/payment /app/services/payment
WORKDIR /app/services/payment

# Run as a non-root user (least privilege).
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# gunicorn handles SIGTERM gracefully: it stops accepting new requests and lets
# in-flight ones finish within --graceful-timeout before exiting. That's the
# "graceful shutdown" requirement for the web process. (Kafka worker processes
# get their own signal handling in Phase 2.)
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "30", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-"]
