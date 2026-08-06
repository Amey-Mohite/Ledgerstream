"""Guardrails for the AI service.

Two layers live here; the third (the tool allowlist) lives in tools.py, and the
fourth (system-prompt injection defense) lives in prompts.py.

1. Per-tenant LLM RATE LIMIT — LLM calls cost money, so cap how many queries a
   tenant can run (token bucket). Over budget → 429.
2. (allowlist + prompt rules are enforced elsewhere; see module docstrings.)
"""

from __future__ import annotations

import time

from fastapi import HTTPException

from app import config

# tenant_id -> (tokens, last_ts).
# ponytail: in-memory per-process bucket; use a shared Redis bucket (like the
# gateway's) if the AI service runs multiple replicas.
_buckets: dict[str, tuple[float, float]] = {}


def check_rate_limit(tenant_id: str) -> None:
    """Spend one token for the tenant; raise 429 if the bucket is empty.

    in : "t-abc"   out: None (allowed) — or raises HTTPException(429, Retry-After).
    e.g. capacity=10: the first 10 quick queries pass; the 11th → 429 until the
    bucket refills at RATE_REFILL_PER_SEC.
    """
    capacity = config.RATE_CAPACITY
    refill = config.RATE_REFILL_PER_SEC
    now = time.time()
    tokens, ts = _buckets.get(tenant_id, (capacity, now))
    tokens = min(capacity, tokens + (now - ts) * refill)
    if tokens < 1:
        retry_after = (1 - tokens) / refill
        raise HTTPException(
            status_code=429,
            detail="LLM rate limit exceeded",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    _buckets[tenant_id] = (tokens - 1, now)
