"""run_with_retry: succeeds within budget, re-raises when exhausted."""

from __future__ import annotations

import pytest

from ledgerstream_shared.kafka import run_with_retry


def test_returns_on_first_success():
    calls = []
    assert run_with_retry(lambda: calls.append(1) or "ok", base_delay=0) == "ok"
    assert len(calls) == 1


def test_retries_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    assert run_with_retry(flaky, max_retries=3, base_delay=0) == "ok"
    assert calls["n"] == 3


def test_reraises_after_exhausting_retries():
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise ValueError("poison")

    with pytest.raises(ValueError):
        run_with_retry(always_fails, max_retries=2, base_delay=0)
    assert calls["n"] == 3   # 1 initial + 2 retries
