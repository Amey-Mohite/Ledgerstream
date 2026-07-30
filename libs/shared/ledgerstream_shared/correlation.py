"""Request-scoped correlation-id propagation.

A correlation id ties together every log line, span, and downstream call that
belongs to a single logical request as it fans across services. We store it in a
`contextvars.ContextVar`, which is the correct primitive here because:

  * it is isolated per-thread AND per-async-task, so a Django worker thread and a
    FastAPI coroutine each see their own value with no bleed-through;
  * it needs no explicit plumbing through every function signature.

This module is framework-neutral on purpose. The Django middleware and the
FastAPI middleware that *populate* this contextvar from an inbound
`X-Correlation-ID` header live in each service (added in Phase 1), so the shared
library never imports a web framework.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

CORRELATION_ID_HEADER = "X-Correlation-ID"

# Empty string == "no correlation id bound yet" (e.g. a worker at startup).
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def new_correlation_id() -> str:
    """Generate a fresh correlation id (used when a request arrives without one)."""
    return uuid.uuid4().hex


def set_correlation_id(value: str) -> Token[str]:
    """Bind a correlation id to the current context.

    Returns the Token so callers (middleware) can reset it after the request,
    preventing leakage into a reused thread/task.
    """
    return _correlation_id.set(value)


def get_correlation_id() -> str:
    """Return the correlation id bound to the current context ('' if none)."""
    return _correlation_id.get()


def reset_correlation_id(token: Token[str]) -> None:
    """Restore the previous correlation id (call in a finally block)."""
    _correlation_id.reset(token)


def ensure_correlation_id(incoming: str | None) -> str:
    """Return `incoming` if it is a non-empty id, else mint a new one, and bind it."""
    value = incoming if incoming else new_correlation_id()
    set_correlation_id(value)
    return value
