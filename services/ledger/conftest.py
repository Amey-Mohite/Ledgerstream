"""Test config + fixtures for the Ledger service (local Postgres, not cloud)."""

from __future__ import annotations

import os

os.environ["LEDGER_DATABASE_URL"] = os.environ.get(
    "TEST_LEDGER_DATABASE_URL",
    "postgresql://ledger:ledger_dev_pw@localhost:5434/ledger",
)
os.environ.setdefault("JWT_SIGNING_KEY", "test-signing-key")

import datetime  # noqa: E402
import uuid  # noqa: E402

import pytest  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def tenant_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def make_event(tenant_id):
    """Build a PaymentCaptured event dict (as the consumer would receive it)."""

    def _make(**overrides):
        event = {
            "event_id": str(uuid.uuid4()),
            "event_version": 1,
            "occurred_at": datetime.datetime.now(datetime.timezone.utc),
            "correlation_id": "",
            "tenant_id": tenant_id,
            "payment_id": str(uuid.uuid4()),
            "amount_minor": 500,
            "currency": "USD",
            "reference": "",
        }
        event.update(overrides)
        return event

    return _make


@pytest.fixture
def auth_client(tenant_id) -> APIClient:
    """A client carrying a JWT (shared signing key) scoped to `tenant_id`."""
    from rest_framework_simplejwt.tokens import AccessToken

    token = AccessToken()
    token["tenant_id"] = tenant_id
    token["user_id"] = "1"
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client
