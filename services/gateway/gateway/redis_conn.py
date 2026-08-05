"""One shared Redis client for the gateway (rate-limit counters + cache).

`redis.from_url` returns a client backed by a connection pool, so a single
module-level client is the right shape — it's thread-safe and pools connections.
`decode_responses=True` gives us `str` back instead of `bytes` (we store JSON
strings and integer counters).
"""

from __future__ import annotations

import redis
from django.conf import settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client
