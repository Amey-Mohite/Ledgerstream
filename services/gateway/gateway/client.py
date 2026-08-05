"""HTTP client to the downstream services.

A thin, transparent forwarder: it takes the incoming method/path/query/body, sends
them to the right backend, and returns the backend's raw response. It also
propagates the correlation id so one request keeps one id across the gateway →
backend hop.

Chunk 4 wraps `forward()` in a **circuit breaker**, so all downstream calls funnel
through this one function on purpose — a single place to add resilience.
"""

from __future__ import annotations

import httpx
from django.conf import settings

from ledgerstream_shared.correlation import CORRELATION_ID_HEADER, get_correlation_id

from .breaker import CircuitOpen, get_breaker

# Only these client headers are forwarded downstream. We deliberately DON'T forward
# Host, Content-Length, or hop-by-hop headers — httpx sets those correctly itself.
_FORWARD_HEADERS = ("authorization", "content-type", "idempotency-key")


class DownstreamError(Exception):
    """A downstream call failed (connection/timeout). The view renders this as 503."""

    def __init__(self, service: str):
        super().__init__(f"downstream '{service}' unavailable")
        self.service = service


def _base_url(service: str) -> str:
    return {
        "payment": settings.PAYMENT_BASE_URL,
        "ledger": settings.LEDGER_BASE_URL,
    }[service]


def forward(
    service: str,
    method: str,
    path: str,
    *,
    query_string: str = "",
    body: bytes = b"",
    headers: dict | None = None,
) -> httpx.Response:
    """Send the request to `service` and return the raw downstream response.

    Wrapped in the service's circuit breaker: if the breaker is OPEN we raise
    `CircuitOpen` WITHOUT calling the backend (fail fast). A connection/timeout, or
    a 5xx, counts as a failure (a downstream fault); 2xx–4xx counts as success (a
    4xx is the *client's* fault, not the backend's, so it must not trip the breaker).
    """
    breaker = get_breaker(service)
    if not breaker.allow():
        raise CircuitOpen(service)

    url = _base_url(service).rstrip("/") + path
    if query_string:
        url = f"{url}?{query_string}"

    fwd = {h: headers[h] for h in _FORWARD_HEADERS if headers and headers.get(h)}
    cid = get_correlation_id()
    if cid:
        fwd[CORRELATION_ID_HEADER] = cid   # carry the id across the hop

    try:
        resp = httpx.request(
            method, url, content=body, headers=fwd, timeout=settings.DOWNSTREAM_TIMEOUT
        )
    except httpx.RequestError as exc:      # connection refused, timeout, DNS, …
        breaker.record_failure()
        raise DownstreamError(service) from exc

    if resp.status_code >= 500:
        breaker.record_failure()           # backend fault
    else:
        breaker.record_success()           # 2xx–4xx → backend is healthy
    return resp
