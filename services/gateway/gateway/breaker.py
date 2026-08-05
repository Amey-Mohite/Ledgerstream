"""A minimal circuit breaker for downstream calls.

A breaker stops the gateway from hammering a backend that's already failing (and
from making every caller wait for a timeout). It has three states:

  CLOSED     normal — calls pass through; count consecutive failures.
  OPEN       too many failures → **fail fast** for `cooldown` seconds without even
             calling the backend (give it room to recover).
  HALF_OPEN  after the cooldown, let ONE probe through: success → CLOSED,
             failure → OPEN again.

There's one breaker per downstream service, so a dead Ledger doesn't trip the
Payment path. State is **in-process** (a module-level dict): each gateway instance
tracks its own view of a backend's health — which is the standard design; a
breaker is a local latency/failure guard, not shared cluster state.

# ponytail: in-memory per-process breaker; use a Redis-backed shared breaker only
# if you specifically need all replicas to trip together (rarely worth it).
"""

from __future__ import annotations

import time

from django.conf import settings


class CircuitOpen(Exception):
    """Raised instead of calling a backend whose breaker is OPEN."""

    def __init__(self, service: str):
        super().__init__(f"circuit open for '{service}'")
        self.service = service


# Worked sequence (threshold=5, cooldown=10s), the "ledger" breaker:
#   t=0    allow()→True, 200 → record_success()      state=closed failures=0
#   ...    5 timeouts in a row, each record_failure() failures=1..5 → OPEN at #5
#   t=2    allow()→False (2s < 10s cooldown)          → fail fast, no backend call → 503
#   t=11   allow()→True  (11s ≥ 10s) flips half_open  → let ONE probe through
#   t=11   probe 200 → record_success()               state=closed  (recovered!)
#          (or probe fails → record_failure() while half_open → straight back to OPEN)
class _Breaker:
    def __init__(self) -> None:
        self.failures = 0
        self.state = "closed"       # closed | open | half_open
        self.opened_at = 0.0        # wall-clock time we tripped to OPEN

    def allow(self) -> bool:
        """Can a call go through right now?"""
        if self.state == "open":
            # e.g. opened_at=0, now=2, cooldown=10 → 2 >= 10 is False → stay open
            if time.time() - self.opened_at >= settings.BREAKER_COOLDOWN:
                self.state = "half_open"    # cooldown elapsed → time to try one probe
                return True
            return False                    # still cooling down → fail fast (→ 503)
        return True                         # closed or half_open → allow

    def record_success(self) -> None:
        # A 2xx–4xx (backend answered) → healthy: clear the count, force CLOSED.
        #   (state, failures): (open, 5) or (half_open, 5) → (closed, 0)
        self.failures = 0
        self.state = "closed"

    def record_failure(self) -> None:
        # A 5xx / timeout / connection error.
        #   closed:    failures 4 → 5, threshold 5 → trip to OPEN (opened_at=now)
        #   half_open: any failure → straight back to OPEN (the probe failed)
        self.failures += 1
        if self.state == "half_open" or self.failures >= settings.BREAKER_THRESHOLD:
            self.state = "open"
            self.opened_at = time.time()


_breakers: dict[str, _Breaker] = {}


def get_breaker(service: str) -> _Breaker:
    # First call for a service creates its breaker; later calls return the SAME
    # object (state persists across requests within this process):
    #   get_breaker("ledger")  → <_Breaker state=closed>   (created + stored)
    #   get_breaker("ledger")  → the same instance          (reused)
    #   get_breaker("payment") → a separate breaker         (independent state)
    return _breakers.setdefault(service, _Breaker())
