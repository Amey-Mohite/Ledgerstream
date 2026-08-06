"""Standalone MCP server exposing the tenant-scoped ledger read tools.

The SAME two read tools the internal loop uses, published over the **Model Context
Protocol** so any MCP client (Claude Desktop, an IDE, another agent) can call them.

Run it (stdio transport):
    LEDGERSTREAM_JWT=<a tenant-scoped access token> python -m app.mcp_server

The operator supplies a tenant-scoped JWT via `LEDGERSTREAM_JWT`; every tool call
forwards it to the Ledger, so the MCP client only ever sees that tenant's data —
the same server-side isolation the HTTP path relies on. (Requires the `mcp`
package: `pip install mcp`.)
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

from app import config

mcp = FastMCP("ledgerstream")


def _jwt() -> str:
    token = os.environ.get("LEDGERSTREAM_JWT")
    if not token:
        raise RuntimeError("set LEDGERSTREAM_JWT to a tenant-scoped access token")
    return token


def _gateway_get(path: str) -> str:
    resp = httpx.get(
        config.GATEWAY_BASE_URL.rstrip("/") + path,
        headers={"Authorization": f"Bearer {_jwt()}"},
        timeout=config.GATEWAY_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


@mcp.tool()
def get_balances() -> str:
    """Get the authenticated tenant's current account balances (JSON)."""
    return _gateway_get("/api/balances")


@mcp.tool()
def get_transactions() -> str:
    """Get the authenticated tenant's recent ledger transactions (JSON)."""
    return _gateway_get("/api/transactions")


if __name__ == "__main__":
    mcp.run()   # stdio transport by default
