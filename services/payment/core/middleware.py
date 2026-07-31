"""Correlation-id middleware.

Binds a correlation id for the duration of each request so every log line and
span produced while handling it is automatically tagged with the same id. If the
caller supplied one (`X-Correlation-ID`), we honour it (a request that started
upstream keeps its id); otherwise we mint one. The id is echoed back on the
response so the caller can log it too.
"""

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
        incoming = request.headers.get(CORRELATION_ID_HEADER)
        correlation_id = incoming or new_correlation_id()
        # Store on the request for handlers; bind to the context for logging.
        request.correlation_id = correlation_id
        token = set_correlation_id(correlation_id)
        try:
            response = self.get_response(request)
            response[CORRELATION_ID_HEADER] = correlation_id
            return response
        finally:
            # Reset so the id never leaks into the next request on a reused thread.
            reset_correlation_id(token)
