"""Correlation-id middleware (same as the Payment service)."""

from __future__ import annotations

from ledgerstream_shared.correlation import (
    CORRELATION_ID_HEADER,
    new_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)


class CorrelationIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or new_correlation_id()
        request.correlation_id = correlation_id
        token = set_correlation_id(correlation_id)
        try:
            response = self.get_response(request)
            response[CORRELATION_ID_HEADER] = correlation_id
            return response
        finally:
            reset_correlation_id(token)
