"""Gateway proxy: the edge-auth gate and transparent routing to the right backend.

Downstreams are mocked (`gateway.client.forward`) so these are pure gateway tests —
no Payment/Ledger/Redis needed.
"""

from __future__ import annotations

import httpx


def _fake_response(status: int = 200, body: bytes = b'{"ok": true}') -> httpx.Response:
    return httpx.Response(
        status_code=status, content=body, headers={"content-type": "application/json"}
    )


def test_protected_route_requires_a_token(api_client, monkeypatch):
    hits = []
    monkeypatch.setattr("gateway.client.forward", lambda *a, **k: hits.append(1) or _fake_response())

    resp = api_client.get("/api/balances")

    assert resp.status_code == 401     # edge auth rejects before any backend call
    assert hits == []                  # backend was never contacted


def test_valid_token_is_proxied_to_the_ledger(auth_client, monkeypatch):
    seen = {}

    def fake(service, method, path, **kw):
        seen.update(service=service, method=method, path=path, headers=kw.get("headers"))
        return _fake_response(body=b'{"balances": []}')

    monkeypatch.setattr("gateway.client.forward", fake)

    resp = auth_client.get("/api/balances?limit=10")

    assert resp.status_code == 200
    assert resp.content == b'{"balances": []}'
    assert seen["service"] == "ledger"
    assert seen["method"] == "GET"
    assert seen["path"] == "/api/balances"
    assert seen["headers"]["authorization"].startswith("Bearer ")   # token forwarded


def test_payments_route_goes_to_the_payment_service(auth_client, monkeypatch):
    seen = {}

    def fake(service, *a, **k):
        seen["service"] = service
        return _fake_response()

    monkeypatch.setattr("gateway.client.forward", fake)

    resp = auth_client.post(
        "/api/payments", {"amount_minor": 500, "currency": "usd"}, format="json"
    )

    assert resp.status_code == 200
    assert seen["service"] == "payment"


def test_auth_route_is_public(api_client, monkeypatch):
    seen = {}

    def fake(service, *a, **k):
        seen["service"] = service
        return _fake_response(body=b'{"access": "jwt"}')

    monkeypatch.setattr("gateway.client.forward", fake)

    # No token — login must pass through without edge auth.
    resp = api_client.post(
        "/api/auth/token", {"username": "u", "password": "p"}, format="json"
    )

    assert resp.status_code == 200
    assert seen["service"] == "payment"
