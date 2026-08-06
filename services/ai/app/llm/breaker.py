"""Per-provider circuit breaker (same pattern as the gateway's, in-process).

Wraps each LLM provider so a failing/slow provider trips and the gateway fails over
to the next one instead of hammering it. State is in-process per instance.
"""

from __future__ import annotations

import time

from app import config


class _Breaker:
    def __init__(self) -> None:
        self.failures = 0
        self.state = "closed"     # closed | open | half_open
        self.opened_at = 0.0

    def allow(self) -> bool:
        """Can the gateway call this provider right now?

        closed              → True  (healthy, let it through)
        open, still cooling → False (skip; caller fails over to next provider)
        open, cooldown done → True, and flips to half_open (one trial call allowed)
        """
        if self.state == "open":
            if time.time() - self.opened_at >= config.BREAKER_COOLDOWN:
                self.state = "half_open"
                return True
            return False
        return True

    def record_success(self) -> None:
        # A call succeeded → fully reset: clear the failure count, close the breaker.
        # (e.g. a half_open trial that worked flips back to "closed".)
        self.failures = 0
        self.state = "closed"

    def record_failure(self) -> None:
        # A call failed → count it. Trip OPEN if we hit the threshold, or if this
        # was the half_open trial (one more failure means "still broken, keep it open").
        # e.g. BREAKER_THRESHOLD=3: failures go 1, 2, 3 → state="open", opened_at=now.
        self.failures += 1
        if self.state == "half_open" or self.failures >= config.BREAKER_THRESHOLD:
            self.state = "open"
            self.opened_at = time.time()


_breakers: dict[str, _Breaker] = {}


def get_breaker(provider_name: str) -> _Breaker:
    # One breaker per provider name, created on first use and reused thereafter.
    # in : "claude"  → the _Breaker tracking Claude's health (same object next call)
    return _breakers.setdefault(provider_name, _Breaker())
