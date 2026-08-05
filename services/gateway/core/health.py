"""Liveness / readiness probes.

Liveness = "the process is up". Readiness = "I can actually serve" — for the
gateway that means **Redis is reachable** (its rate-limit + cache store). We do NOT
check the downstream services here: a gateway should stay *ready* even when a backend
is down — routing to a dead backend is the circuit breaker's job, not readiness's.
Coupling readiness to downstreams would let one backend outage take the gateway out
of the load-balancer too.
"""

from __future__ import annotations

from django.http import JsonResponse

from gateway.redis_conn import get_redis


def liveness(request) -> JsonResponse:
    return JsonResponse({"status": "alive"})


def readiness(request) -> JsonResponse:
    ok = _redis_ok()
    return JsonResponse(
        {"status": "ready" if ok else "not_ready", "checks": {"redis": ok}},
        status=200 if ok else 503,
    )


def _redis_ok() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:  # noqa: BLE001
        return False
