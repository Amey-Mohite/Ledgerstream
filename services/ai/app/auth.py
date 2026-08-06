"""Edge JWT auth for the AI service — stateless, shared-key (same trust model).

A FastAPI dependency that verifies the Bearer token's signature + expiry with the
shared `JWT_SIGNING_KEY` and returns a `Principal`. We keep the RAW token too,
because the tenant-scoped ledger tools re-present it to the Ledger read API — the
LLM only ever sees data for the caller's own tenant.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException

from app import config


@dataclass
class Principal:
    tenant_id: str
    token: str          # the verified JWT, forwarded to the Ledger by the tools


def require_principal(authorization: str | None = Header(default=None)) -> Principal:
    # in : the Authorization header, e.g. "Bearer eyJhbGci...c2Vk"
    # out: Principal(tenant_id="t-abc", token="eyJ...")  — or raises 401 / 403
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")     # no header → 401
    token = authorization.split(" ", 1)[1]                                       # "eyJhbGci...c2Vk"
    try:
        # jwt.decode recomputes the HMAC signature with the shared key and checks
        # exp — no DB lookup (see concepts/authentication-and-jwt.md).
        #   → claims dict, e.g. {"user_id":"1", "tenant_id":"t-abc", "exp":1735689600}
        claims = jwt.decode(token, config.JWT_SIGNING_KEY, algorithms=[config.JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid or expired token")  # bad sig / expired → 401
    tenant_id = claims.get("tenant_id")                                          # "t-abc"
    if not tenant_id:
        raise HTTPException(status_code=403, detail="token is not scoped to a tenant")  # no tenant → 403
    return Principal(tenant_id=tenant_id, token=token)
