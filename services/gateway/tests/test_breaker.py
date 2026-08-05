"""Circuit breaker: the state machine + the 503 the gateway returns."""

from __future__ import annotations

import time

from gateway.breaker import CircuitOpen, _Breaker
from gateway.client import DownstreamError


def test_state_machine_opens_then_probes_then_closes(settings):
    settings.BREAKER_THRESHOLD = 3
    settings.BREAKER_COOLDOWN = 0.05

    b = _Breaker()
    assert b.allow()                    # starts closed

    for _ in range(3):
        b.record_failure()
    assert b.state == "open"
    assert not b.allow()                # OPEN → fail fast during cooldown

    time.sleep(0.06)
    assert b.allow()                    # cooldown elapsed → allow one probe
    assert b.state == "half_open"

    b.record_success()                  # probe succeeded → back to normal
    assert b.state == "closed"


def test_half_open_probe_failure_reopens(settings):
    settings.BREAKER_THRESHOLD = 1
    settings.BREAKER_COOLDOWN = 0.01
    b = _Breaker()
    b.record_failure()                  # threshold 1 → open
    time.sleep(0.02)
    assert b.allow()                    # half_open
    b.record_failure()                  # probe fails → reopen
    assert b.state == "open"


def test_downstream_error_becomes_503(auth_client, fake_redis, monkeypatch):
    monkeypatch.setattr(
        "gateway.client.forward", lambda *a, **k: (_ for _ in ()).throw(DownstreamError("ledger"))
    )
    resp = auth_client.get("/api/balances")
    assert resp.status_code == 503


def test_open_circuit_becomes_503(auth_client, fake_redis, monkeypatch):
    monkeypatch.setattr(
        "gateway.client.forward", lambda *a, **k: (_ for _ in ()).throw(CircuitOpen("ledger"))
    )
    resp = auth_client.get("/api/balances")
    assert resp.status_code == 503
