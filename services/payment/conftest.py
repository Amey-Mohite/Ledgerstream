"""Test configuration + shared fixtures.

Tests run against a LOCAL Postgres (not the cloud Neon DB): cloud latency +
per-run test-database creation would make the suite slow and flaky, and our
design doc records that tests use local infra. We force the DB URL here, before
Django settings load, so a stray `.env` can't point tests at production.

Start the DB first:  docker compose --profile full up -d postgres-payment
"""

from __future__ import annotations

import os

# Force tests onto local Postgres regardless of what .env holds.
os.environ["PAYMENT_DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://payment:payment_dev_pw@localhost:5433/payment",
)
# Ensure required non-DB settings exist even if .env is absent in CI.
os.environ.setdefault("JWT_SIGNING_KEY", "test-signing-key")

import pytest  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def make_tenant(db):
    """Create a tenant + user + membership and return (tenant, authed_client).

    Each call yields an independent tenant with its own JWT-authenticated client,
    so tests can pit tenant A against tenant B.
    """
    from django.contrib.auth import get_user_model

    from core.tokens import TenantTokenObtainPairSerializer
    from tenants.models import Membership, Tenant

    User = get_user_model()

    def _make(name: str):
        tenant = Tenant.objects.create(name=name)
        user = User.objects.create_user(username=f"user-{tenant.id}", password="pw")
        Membership.objects.create(user=user, tenant=tenant)

        token = TenantTokenObtainPairSerializer.get_token(user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
        return tenant, client

    return _make
