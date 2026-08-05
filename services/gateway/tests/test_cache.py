"""Cache-aside on balances: HIT skips the backend; a write invalidates."""

from __future__ import annotations

import httpx


def _resp(body: bytes = b'{"balances": [1]}') -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "application/json"})


def test_balances_hit_is_served_without_touching_the_backend(auth_client, fake_redis, monkeypatch):
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return _resp()

    monkeypatch.setattr("gateway.client.forward", fake)

    r1 = auth_client.get("/api/balances")
    r2 = auth_client.get("/api/balances")

    assert r1.headers["X-Cache"] == "MISS"
    assert r2.headers["X-Cache"] == "HIT"
    assert r2.content == r1.content
    assert calls["n"] == 1                 # backend called once; 2nd served from cache


def test_a_write_invalidates_the_balances_cache(auth_client, fake_redis, monkeypatch):
    monkeypatch.setattr("gateway.client.forward", lambda *a, **k: _resp())

    auth_client.get("/api/balances")       # populate cache
    # A capture (write to payments) invalidates the tenant's cached balances.
    auth_client.post("/api/payments", {"amount_minor": 1, "currency": "usd"}, format="json")
    r = auth_client.get("/api/balances")

    assert r.headers["X-Cache"] == "MISS"  # cache was dropped → re-fetched
