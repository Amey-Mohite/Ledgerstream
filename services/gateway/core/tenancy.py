"""Tenant resolution from the validated JWT (same rule as Payment/Ledger).

The gateway reads `tenant_id` from the verified token to key per-tenant rate limits
and cache entries — never from a client-supplied header (which could be forged).
"""

from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

TENANT_CLAIM = "tenant_id"


def require_tenant_id(request) -> str:
    # request.auth = the validated JWT for this request. Its claims look like:
    #   {"token_type": "access", "user_id": "1",
    #    "tenant_id": "t-abc", "exp": 1735689600, "jti": "…"}
    # token["tenant_id"] → "t-abc"  → returned, and used to scope EVERY DB query.
    token = getattr(request, "auth", None)
    tenant_id = token[TENANT_CLAIM] if token else None
    # No token, or a token with no tenant_id claim → we can't safely scope data,
    # so refuse rather than risk leaking another tenant's rows.
    #   → raises PermissionDenied → HTTP 403
    if not tenant_id:
        raise PermissionDenied("Authenticated token is not scoped to a tenant.")
    return tenant_id
