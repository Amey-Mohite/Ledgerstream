"""The tenant-scoped read tools the LLM may call, plus the allowlist guardrail.

The model NEVER writes SQL and NEVER picks a tenant. It can only call these two
read tools, which are executed **through the API Gateway** using the CALLER'S JWT
(AI -> Gateway -> Ledger). Tenant isolation is enforced server-side (the gateway
validates + forwards the JWT; the Ledger scopes by tenant), not by the model's
goodwill — and the reads inherit the gateway's rate limit / cache / breaker. An
unknown/injected tool name is rejected by the allowlist.
"""

from __future__ import annotations

import logging

import httpx

from app import config
from app.auth import Principal
from app.llm.base import ToolSpec

logger = logging.getLogger(__name__)

_EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}

BALANCES = ToolSpec(
    name="get_balances",
    description="Get the authenticated tenant's current account balances "
                "(e.g. CASH, MERCHANT_PAYABLE). Returns JSON.",
    input_schema=_EMPTY_SCHEMA,
)
TRANSACTIONS = ToolSpec(
    name="get_transactions",
    description="Get the authenticated tenant's recent ledger transactions "
                "(journal history, newest first). Returns JSON.",
    input_schema=_EMPTY_SCHEMA,
)


def _gateway_get(path: str, principal: Principal) -> str:
    """GET a read endpoint via the API Gateway with the caller's JWT. Returns the body.

    The gateway mirrors the backend URL scheme, so `/api/balances` and
    `/api/transactions` route straight through to the Ledger (tenant-scoped).

    in : ("/api/balances", principal)
    out: the response body, e.g.
      '[{"code":"CASH","balance":500}, {"code":"MERCHANT_PAYABLE","balance":-500}]'
    """
    resp = httpx.get(
        config.GATEWAY_BASE_URL.rstrip("/") + path,
        headers={"Authorization": f"Bearer {principal.token}"},
        timeout=config.GATEWAY_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


class ToolRegistry:
    def __init__(self) -> None:
        self._specs = [BALANCES, TRANSACTIONS]
        self._allowed = {"get_balances", "get_transactions"}   # the guardrail allowlist

    def specs(self) -> list[ToolSpec]:
        return self._specs

    def execute(self, name: str, tool_input: dict, principal: Principal) -> str:
        # in : ("get_balances", {}, principal)
        # out: the ledger JSON string, e.g. '[{"code":"CASH","balance":500}]'
        #      ("rm_rf", ...) → "error: tool 'rm_rf' is not permitted"  (allowlist block)
        #
        # Guardrail: only ever run an allowlisted read tool. A model that's been
        # tricked into requesting anything else gets a harmless error, not execution.
        if name not in self._allowed:
            logger.warning("blocked non-allowlisted tool", extra={"tool": name})
            return f"error: tool '{name}' is not permitted"
        if name == "get_balances":
            return _gateway_get("/api/balances", principal)
        if name == "get_transactions":
            return _gateway_get("/api/transactions", principal)
        return "error: unknown tool"
