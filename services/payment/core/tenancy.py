"""Tenant resolution + enforcement.

Multi-tenancy rule: every authenticated request belongs to exactly one tenant,
carried as a `tenant_id` claim inside the JWT (put there at login — see
core/tokens.py). We read it from the *validated* token, never from a header or
query param the client could forge.

`require_tenant_id` is the single choke point every view uses, so tenant scoping
can't be forgotten in one place and silently leak data.
"""

from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

TENANT_CLAIM = "tenant_id"


def get_tenant_id(request) -> str | None:
    """Return the tenant_id from the validated JWT, or None."""
    token = getattr(request, "auth", None)  # SimpleJWT token instance
    if token is None:
        return None
    try:
        return token[TENANT_CLAIM]
    except (KeyError, TypeError):
        return None


def require_tenant_id(request) -> str:
    """Return the request's tenant_id or raise 403 if absent.

    A token without a tenant is not allowed to touch tenant-scoped data — fail
    closed, never open.
    """
    tenant_id = get_tenant_id(request)
    if not tenant_id:
        raise PermissionDenied("Authenticated token is not scoped to a tenant.")
    return tenant_id
