"""Config for the AI Query service (FastAPI) — env-driven, 12-factor.

The AI service is stateless: it validates JWTs with the shared key, calls an LLM
provider, and reads a tenant's ledger **through the API Gateway** (AI -> Gateway ->
Ledger) — never the Ledger directly — so those reads get the gateway's edge auth,
per-tenant rate limiting, balances caching, and circuit breaker. No database.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from ledgerstream_shared.config import get_env, require_env

REPO_ROOT = Path(__file__).resolve().parents[3]   # services/ai/app -> repo root
load_dotenv(REPO_ROOT / ".env")

# --- Auth (same stateless JWT trust as the other services) -------------------
JWT_SIGNING_KEY = require_env("JWT_SIGNING_KEY")
JWT_ALGORITHM = "HS256"

# --- Downstream: reads go through the API Gateway (AI -> Gateway -> Ledger) --
# The AI service calls the SAME public gateway every other caller uses, keyed by
# the caller's JWT — not the Ledger directly. So its ledger reads reuse the
# gateway's rate limit, balances cache, and breaker instead of re-inventing them.
GATEWAY_BASE_URL = get_env("GATEWAY_BASE_URL", "http://localhost:8010")
GATEWAY_TIMEOUT = float(get_env("GATEWAY_TIMEOUT", "5.0"))

# --- LLM providers -----------------------------------------------------------
# Real providers are used when their key is present; otherwise the mock provider
# keeps the service (and tests) runnable with no keys. Default provider order is
# tried in sequence (failover) — see llm/gateway.py.
ANTHROPIC_API_KEY = get_env("ANTHROPIC_API_KEY")   # optional
OPENAI_API_KEY = get_env("OPENAI_API_KEY")          # optional
CLAUDE_MODEL = get_env("CLAUDE_MODEL", "claude-opus-5")
OPENAI_MODEL = get_env("OPENAI_MODEL", "gpt-4.1")
LLM_TIMEOUT = float(get_env("LLM_TIMEOUT", "30.0"))
LLM_MAX_TOKENS = int(get_env("LLM_MAX_TOKENS", "1024"))
# Comma-separated preference order, e.g. "claude,openai,mock".
LLM_PROVIDER_ORDER = get_env("LLM_PROVIDER_ORDER", "claude,openai,mock")

# --- Resilience: circuit breaker per provider --------------------------------
BREAKER_THRESHOLD = int(get_env("AI_BREAKER_THRESHOLD", "3"))
BREAKER_COOLDOWN = float(get_env("AI_BREAKER_COOLDOWN", "15.0"))

# --- Guardrail: per-tenant LLM rate limit (cost control; token bucket) --------
RATE_CAPACITY = int(get_env("AI_RATE_CAPACITY", "10"))          # burst of LLM queries
RATE_REFILL_PER_SEC = float(get_env("AI_RATE_REFILL_PER_SEC", "0.2"))  # ~12/min sustained

# --- Observability -----------------------------------------------------------
SERVICE_NAME = "ai-service"
LOG_LEVEL = get_env("LOG_LEVEL", "INFO")
