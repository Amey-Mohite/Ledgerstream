"""Correlation-id middleware for FastAPI (ASGI) — reuses the shared primitive.

Same idea as the Django services' middleware, in Starlette form: read or mint the
id, bind it into the contextvar so every log line carries it, echo it back, and
reset it in a finally so it can't leak across requests on a reused worker.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware

from ledgerstream_shared.correlation import (
    CORRELATION_ID_HEADER,
    new_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or new_correlation_id()
        token = set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
            response.headers[CORRELATION_ID_HEADER] = correlation_id
            return response
        finally:
            reset_correlation_id(token)
