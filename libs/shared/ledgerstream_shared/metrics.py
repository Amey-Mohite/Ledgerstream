"""Prometheus metric helpers.

We expose metrics two ways depending on the process type:
  * HTTP services (Django/FastAPI) serve a `/metrics` endpoint — but generating
    that text is the framework's job, so here we just provide the shared
    registry + metric factories.
  * Worker processes (Kafka consumers, outbox relay) have no HTTP server, so
    `start_metrics_server` spins up a tiny standalone exporter for them.

Keeping metric *definitions* here means the `service`/metric names are
consistent across every process without copy-paste.
"""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    start_http_server,
)

# A dedicated registry (not the global default) makes tests deterministic and
# avoids double-registration errors when modules are imported repeatedly.
REGISTRY = CollectorRegistry()

# Latency buckets tuned for API/DB work (1ms .. 5s). Adjust per-service later.
_LATENCY_BUCKETS = (
    0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0,
)


def request_counter() -> Counter:
    """Total requests, labelled by service/method/route/status."""
    return Counter(
        "ledgerstream_requests_total",
        "Total requests handled",
        labelnames=("service", "method", "route", "status"),
        registry=REGISTRY,
    )


def request_latency() -> Histogram:
    """Request latency in seconds, labelled by service/method/route."""
    return Histogram(
        "ledgerstream_request_duration_seconds",
        "Request duration in seconds",
        labelnames=("service", "method", "route"),
        buckets=_LATENCY_BUCKETS,
        registry=REGISTRY,
    )


def events_consumed_counter() -> Counter:
    """Kafka events consumed, labelled by service/topic/outcome (Phase 2+)."""
    return Counter(
        "ledgerstream_events_consumed_total",
        "Kafka events consumed",
        labelnames=("service", "topic", "outcome"),
        registry=REGISTRY,
    )


def start_metrics_server(port: int) -> None:
    """Start a standalone Prometheus HTTP exporter (for worker processes)."""
    start_http_server(port, registry=REGISTRY)
