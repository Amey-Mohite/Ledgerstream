"""Structured JSON logging with automatic correlation-id injection.

Why JSON logs: in a multi-service system you ship logs to a central store and
query them by field (`service`, `correlation_id`, `level`). Human-formatted
lines force brittle regex parsing; JSON is queryable out of the box.

Why a custom formatter instead of python-json-logger: one small, dependency-free
class keeps the shared library lean and makes the exact output contract explicit
and testable (see tests/test_logging.py).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
from typing import Any

from ledgerstream_shared.correlation import get_correlation_id

# Attributes present on every stdlib LogRecord; anything NOT in here that a
# caller passed via `extra=` is treated as a structured field and included.
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": record.getMessage(),
            # Correlation id is pulled from the ambient context, so callers never
            # have to remember to pass it — every line is automatically tied to
            # its request.
            "correlation_id": get_correlation_id(),
        }

        # Merge any structured fields passed via logger.info("msg", extra={...}).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(service_name: str, level: str = "INFO") -> logging.Logger:
    """Install the JSON formatter on the root logger for a service.

    Idempotent: calling twice replaces handlers rather than stacking them, so a
    reload or repeated import doesn't produce duplicate log lines.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter(service_name))

    root.handlers.clear()
    root.addHandler(handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a named logger (child of the root configured above)."""
    return logging.getLogger(name)
