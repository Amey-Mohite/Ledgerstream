"""Tenant resolution from the validated JWT (same rule as Payment)."""

from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

TENANT_CLAIM = "tenant_id"


def require_tenant_id(request) -> str:
    token = getattr(request, "auth", None)
    tenant_id = token[TENANT_CLAIM] if token else None
    if not tenant_id:
        raise PermissionDenied("Authenticated token is not scoped to a tenant.")
    return tenant_id
