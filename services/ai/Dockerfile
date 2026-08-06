# AI Query service image (FastAPI, stateless).
#
# BUILD FROM THE REPO ROOT so the shared library is in the build context:
#   docker build -f services/ai/Dockerfile -t ledgerstream/ai .

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1. Shared library first (changes rarely, caches well).
COPY libs/shared /app/libs/shared
RUN pip install /app/libs/shared

# 2. Service dependencies (separate cached layer).
COPY services/ai/requirements.txt /app/services/ai/requirements.txt
RUN pip install -r /app/services/ai/requirements.txt

# 3. Service code last.
COPY services/ai /app/services/ai
WORKDIR /app/services/ai

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8030

# uvicorn serves the ASGI app; graceful shutdown on SIGTERM by default.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8030"]
