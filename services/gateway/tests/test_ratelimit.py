"""Rate limiting: the 429 path (mocked) + the real token-bucket Lua (fakeredis).

The bucket test runs the ACTUAL Lua script against an in-memory, Lua-capable
`fakeredis`, so it verifies real behaviour with no external Redis needed.
"""

from __future__ import annotations

import uuid

import pytest


def test_over_limit_returns_429_with_retry_after(auth_client, monkeypatch):
    # Force the limiter to deny (no Redis needed).
    monkeypatch.setattr("gateway.ratelimit.check", lambda identity: (False, 2.0))

    resp = auth_client.get("/api/balances")

    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") is not None   # tells the client when to retry


@pytest.mark.real_ratelimit   # opt out of the autouse "allow" mock — exercise the real bucket
def test_token_bucket_allows_a_burst_then_blocks(settings):
    """capacity=3, ~no refill → 3 allowed, 4th blocked with a positive wait."""
    import fakeredis

    from gateway import ratelimit, redis_conn

    redis_conn._client = fakeredis.FakeStrictRedis(decode_responses=True)  # Lua-capable
    settings.RATE_LIMIT_CAPACITY = 3
    settings.RATE_LIMIT_REFILL_PER_SEC = 0.0001   # effectively frozen during the test

    identity = f"test:{uuid.uuid4()}"
    outcomes = [ratelimit.check(identity) for _ in range(4)]

    assert [allowed for allowed, _ in outcomes] == [True, True, True, False]
    assert outcomes[3][1] > 0                     # 4th call reports a wait
