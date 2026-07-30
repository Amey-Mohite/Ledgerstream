# ledgerstream-shared

Cross-service primitives shared by every Ledgerstream service. **No business
logic, no web-framework imports** — pulling this into one service never drags in
another service's stack.

| Module | Purpose |
|---|---|
| `config` | Typed, fail-fast environment access (`require_env`, `get_int`, …). |
| `correlation` | Request-scoped correlation-id via `contextvars` (framework-neutral). |
| `logging` | Structured JSON logs with automatic correlation-id injection. |
| `metrics` | Prometheus registry + metric factories; standalone exporter for workers. |
| `tracing` | OpenTelemetry tracer (OTLP → collector), degrades to console locally. |

## Local use

```bash
pip install -e libs/shared[dev]
pytest libs/shared
```

Each service installs this package (editable) in its own image and calls, at
startup:

```python
from ledgerstream_shared.logging import configure_logging
from ledgerstream_shared.tracing import configure_tracing

configure_logging("payment-service", level="INFO")
configure_tracing("payment-service")
```

The Django/FastAPI middleware that reads the inbound `X-Correlation-ID` header
and binds it via `correlation.ensure_correlation_id()` lives in each service
(added in Phase 1) — that's the boundary that keeps this library framework-free.
