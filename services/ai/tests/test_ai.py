"""AI Query service — edge auth, the tool-use loop, guardrails, and failover.

Hermetic: the mock provider drives the real loop, and the Ledger call is monkey-
patched, so no network / API keys are needed.
"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app import config
from app.auth import Principal
from app.main import app

client = TestClient(app)


def _token(tenant: str = "t-1") -> str:
    return jwt.encode(
        {"tenant_id": tenant, "user_id": "1", "exp": int(time.time()) + 3600},
        config.JWT_SIGNING_KEY,
        algorithm="HS256",
    )


def _auth(tenant: str = "t-1") -> dict:
    return {"Authorization": f"Bearer {_token(tenant)}"}


# --- edge auth ---------------------------------------------------------------

def test_health():
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_requires_a_token():
    assert client.post("/api/ai/query", json={"question": "cash balance?"}).status_code == 401


def test_rejects_token_without_tenant():
    bad = jwt.encode({"user_id": "1", "exp": int(time.time()) + 3600},
                     config.JWT_SIGNING_KEY, algorithm="HS256")
    resp = client.post("/api/ai/query", json={"question": "hi"},
                       headers={"Authorization": f"Bearer {bad}"})
    assert resp.status_code == 403


# --- the tool-use loop (grounded answers) ------------------------------------

def test_balance_question_calls_get_balances_and_grounds_the_answer(monkeypatch):
    monkeypatch.setattr("app.tools._gateway_get",
                        lambda path, principal: '[{"code":"CASH","balance":500}]')
    resp = client.post("/api/ai/query", json={"question": "what's my cash balance?"},
                       headers=_auth("t-a"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "mock"
    assert "get_balances" in body["tools_used"]
    assert "500" in body["answer"]                 # answer grounded in the tool result


def test_transactions_question_calls_get_transactions(monkeypatch):
    monkeypatch.setattr("app.tools._gateway_get",
                        lambda path, principal: '{"results": [{"amount": 700}]}')
    resp = client.post("/api/ai/query", json={"question": "show my recent transactions"},
                       headers=_auth("t-b"))
    assert resp.status_code == 200
    assert "get_transactions" in resp.json()["tools_used"]


def test_off_topic_question_uses_no_tools(monkeypatch):
    resp = client.post("/api/ai/query", json={"question": "what's the weather in Paris?"},
                       headers=_auth("t-c"))
    assert resp.status_code == 200
    assert resp.json()["tools_used"] == []


# --- guardrails --------------------------------------------------------------

def test_tool_allowlist_blocks_unknown_tools():
    from app.tools import ToolRegistry
    out = ToolRegistry().execute("rm_rf", {}, Principal(tenant_id="t", token="x"))
    assert "not permitted" in out                  # never executed


def test_per_tenant_rate_limit_returns_429(monkeypatch):
    monkeypatch.setattr(config, "RATE_CAPACITY", 1)
    monkeypatch.setattr(config, "RATE_REFILL_PER_SEC", 0.0001)   # effectively no refill
    # "hello" needs no tool, so no ledger call.
    first = client.post("/api/ai/query", json={"question": "hello"}, headers=_auth("rl-1"))
    second = client.post("/api/ai/query", json={"question": "hello"}, headers=_auth("rl-1"))
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers.get("Retry-After") is not None


# --- provider failover -------------------------------------------------------

def test_gateway_fails_over_to_the_next_provider(monkeypatch):
    from app.llm.gateway import Gateway
    from app.llm.mock import MockProvider

    class _Boom:
        name = "boom"
        def generate(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr("app.tools._gateway_get",
                        lambda path, principal: '[{"code":"CASH","balance":42}]')
    g = Gateway()
    g._providers = [_Boom(), MockProvider()]        # first fails, second (mock) succeeds

    text, provider, tools_used = g.answer("cash balance?", Principal(tenant_id="t", token="x"))
    assert provider == "mock"
    assert "42" in text
