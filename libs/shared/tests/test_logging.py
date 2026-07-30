"""Tests for structured JSON logging + correlation-id injection."""

from __future__ import annotations

import json
import logging

from ledgerstream_shared.correlation import reset_correlation_id, set_correlation_id
from ledgerstream_shared.logging import JsonFormatter, configure_logging


def _format(record: logging.LogRecord, service: str = "test-svc") -> dict:
    return json.loads(JsonFormatter(service).format(record))


def _make_record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.module",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_output_is_valid_json_with_core_fields():
    payload = _format(_make_record())
    assert payload["level"] == "INFO"
    assert payload["service"] == "test-svc"
    assert payload["logger"] == "app.module"
    assert payload["message"] == "hello world"  # args are interpolated
    assert "timestamp" in payload


def test_correlation_id_is_injected_from_context():
    token = set_correlation_id("corr-123")
    try:
        payload = _format(_make_record())
    finally:
        reset_correlation_id(token)
    assert payload["correlation_id"] == "corr-123"


def test_correlation_id_empty_when_unbound():
    payload = _format(_make_record())
    assert payload["correlation_id"] == ""


def test_extra_fields_are_included():
    payload = _format(_make_record(tenant_id="t-1", amount=500))
    assert payload["tenant_id"] == "t-1"
    assert payload["amount"] == 500


def test_configure_logging_is_idempotent():
    root = configure_logging("svc", "INFO")
    first = len(root.handlers)
    root = configure_logging("svc", "DEBUG")
    assert len(root.handlers) == first == 1
