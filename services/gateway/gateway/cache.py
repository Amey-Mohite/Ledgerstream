"""Cache-aside for read responses, backed by Redis.

Cache-aside (a.k.a. lazy caching): the application checks the cache first; on a
**miss** it fetches from the source, stores the result, and returns it; on a
**hit** it returns the cached copy without touching the backend. Two ways an entry
leaves the cache:
  * **TTL expiry** — the real staleness bound (bounded-staleness). A cached balance
    can be at most `ttl` seconds old.
  * **Explicit invalidation** — on a write that changes the data (a capture), we
    delete the tenant's cached balances so the next read re-fetches.

Entries are keyed **per tenant** so one tenant never sees another's data.
"""

from __future__ import annotations

from gateway.redis_conn import get_redis


def _key(name: str, tenant: str) -> str:
    # ("balances", "tenant-123") → "cache:balances:tenant-123"
    # The tenant is baked into the key → tenant A can never read tenant B's cache.
    return f"cache:{name}:{tenant}"


def get(name: str, tenant: str) -> str | None:
    """Return the cached body (str) or None on a miss.

    Redis command: GET "cache:balances:tenant-123"
      HIT  → '[{"code":"CASH","balance":500}, ...]'   (the JSON string we stored)
      MISS → None   (never cached, or the TTL already expired it)
    """
    return get_redis().get(_key(name, tenant))


def set(name: str, tenant: str, body: str, ttl: int) -> None:
    """Store the body with a TTL (SET key val EX ttl → set + expire atomically).

    Redis command: SET "cache:balances:tenant-123" '<json>' EX 30
      → writes the value AND schedules auto-delete in 30s, in one command.
      → after 30s the key is gone, so the next get() is a MISS (bounded staleness).
    """
    get_redis().set(_key(name, tenant), body, ex=ttl)


def invalidate(name: str, tenant: str) -> None:
    """Drop the tenant's cached entry so the next read re-fetches from the source.

    Redis command: DEL "cache:balances:tenant-123"
      → returns 1 if a key was removed, 0 if it wasn't there (harmless either way).
      → the very next get() for that tenant is a MISS → re-fetched fresh.
    """
    get_redis().delete(_key(name, tenant))
