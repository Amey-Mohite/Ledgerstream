"""FastAPI app for the AI Query service (the one non-Django service).

Stateless: edge JWT auth + LLM gateway + tenant-scoped ledger tools. Health
readiness reports 'ready' (its only hard dependency, the Ledger, is guarded by a
circuit breaker at call time, not at readiness — same rule as the gateway).
"""

from __future__ import annotations

from fastapi import FastAPI

from app import config
from app.middleware import CorrelationIdMiddleware
from app.router import router
from ledgerstream_shared.logging import configure_logging
from ledgerstream_shared.tracing import configure_tracing

configure_logging(config.SERVICE_NAME, level=config.LOG_LEVEL)
configure_tracing(config.SERVICE_NAME)

app = FastAPI(title="Ledgerstream — AI Query")
app.add_middleware(CorrelationIdMiddleware)
app.include_router(router)


@app.get("/health/live")
def liveness() -> dict:
    return {"status": "alive"}


@app.get("/health/ready")
def readiness() -> dict:
    return {"status": "ready"}
