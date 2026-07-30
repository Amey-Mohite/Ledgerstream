"""Ledgerstream shared primitives.

This package is the ONE explicit dependency shared across services. It carries
no business logic and no framework coupling (no Django, no FastAPI imports) so
that pulling it into any service never drags in another service's stack.

Public surface:
    - config:      typed environment access
    - correlation: request-scoped correlation-id propagation (contextvars)
    - logging:     structured JSON logging with correlation-id injection
    - metrics:     Prometheus metric helpers
    - tracing:     OpenTelemetry tracer setup (OTLP -> collector)
"""

from ledgerstream_shared import config, correlation, logging, metrics, tracing

__all__ = ["config", "correlation", "logging", "metrics", "tracing"]
__version__ = "0.1.0"
