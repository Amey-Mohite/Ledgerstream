"""Tests for correlation-id context propagation."""

from __future__ import annotations

from ledgerstream_shared.correlation import (
    ensure_correlation_id,
    get_correlation_id,
    new_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)


def test_default_is_empty():
    # A fresh context has no bound id.
    assert get_correlation_id() == ""


def test_set_and_get():
    token = set_correlation_id("abc")
    try:
        assert get_correlation_id() == "abc"
    finally:
        reset_correlation_id(token)
    assert get_correlation_id() == ""


def test_new_correlation_id_is_unique_hex():
    a, b = new_correlation_id(), new_correlation_id()
    assert a != b
    assert len(a) == 32  # uuid4().hex
    int(a, 16)  # parses as hex


def test_ensure_uses_incoming_when_present():
    token = set_correlation_id("")
    try:
        assert ensure_correlation_id("incoming-id") == "incoming-id"
        assert get_correlation_id() == "incoming-id"
    finally:
        reset_correlation_id(token)


def test_ensure_mints_when_absent():
    token = set_correlation_id("")
    try:
        value = ensure_correlation_id(None)
        assert value != ""
        assert get_correlation_id() == value
    finally:
        reset_correlation_id(token)
