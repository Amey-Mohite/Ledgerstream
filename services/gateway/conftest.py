"""Test config + fixtures for the Gateway (no database — it's stateless).

Set the signing key BEFORE settings import so the token we mint and the gateway's
validator share it. `load_dotenv(override=False)` in settings won't clobber it.
"""

from __future__ import annotations

import os

os.environ.setdefault("JWT_SIGNING_KEY", "test-signing-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402


@pytest.fixture(autouse=True)
def _rate_limit_allows(request, monkeypatch):
    """Default: the rate limiter allows everything, so unit tests need no Redis.
    Opt out with @pytest.mark.real_ratelimit to exercise the real token bucket."""
    if "real_ratelimit" in request.keywords:
        return
    monkeypatch.setattr("gateway.ratelimit.check", lambda identity: (True, 0.0))


@pytest.fixture
def fake_redis():
    """Point the gateway's Redis client at an in-memory, Lua-capable fake."""
    import fakeredis

    from gateway import redis_conn

    redis_conn._client = fakeredis.FakeStrictRedis(decode_responses=True)
    yield redis_conn._client
    redis_conn._client = None


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def auth_client() -> APIClient:
    """A client carrying a valid JWT (shared key) scoped to a tenant."""
    from rest_framework_simplejwt.tokens import AccessToken

    token = AccessToken()
    token["tenant_id"] = "tenant-123"
    token["user_id"] = "1"
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client
