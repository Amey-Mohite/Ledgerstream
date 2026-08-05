"""Correlation-id middleware — ORIGINATES the id at the edge.

The gateway is the front door, so it's usually where a request first enters the
system. If the client didn't send an `X-Correlation-ID`, we mint one here and it
then flows to every downstream service (the proxy forwards the header) and every
log line — one id ties the whole cross-service request together.
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
        # Inbound "X-Correlation-ID: abc-123" present → correlation_id = "abc-123"
        # Absent (client's first hop)              → new_correlation_id() = "9f2c4e…e1"
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or new_correlation_id()
        request.correlation_id = correlation_id       # handy for views
        # Bind it into the contextvar so EVERY log line in this request auto-carries it.
        # set_correlation_id returns a Token that remembers the PREVIOUS value.
        token = set_correlation_id(correlation_id)
        try:
            response = self.get_response(request)     # run the view (→ proxy → backend)
            response[CORRELATION_ID_HEADER] = correlation_id   # echo it back to the client
            return response
        finally:
            # Restore the contextvar so this id can't leak into the NEXT request that
            # reuses this worker thread. (Full explanation: observability.md §3.5.)
            reset_correlation_id(token)
