"""The LLM gateway: multi-provider selection, failover, and the tool-use loop.

- Builds the provider chain from LLM_PROVIDER_ORDER, including only providers whose
  key is set (mock is always available as the final failover).
- `answer()` tries providers in order; a provider whose breaker is OPEN is skipped,
  and a provider that raises trips its breaker and fails over to the next.
- `_run_loop()` is the provider-neutral **tool-use loop**: ask the model → if it
  requests tools, execute the (allowlisted, tenant-scoped) tools and feed the
  results back → repeat until a final answer or the tool budget is spent.
"""

from __future__ import annotations

import logging

from app import config
from app.auth import Principal
from app.llm.breaker import get_breaker
from app.llm.mock import MockProvider
from app.prompts import SYSTEM_PROMPT
from app.tools import ToolRegistry

logger = logging.getLogger(__name__)

_MAX_TOOL_ITERATIONS = 4


def _build_providers() -> list:
    """Build the ordered provider chain from LLM_PROVIDER_ORDER, keeping only
    providers that can actually run (claude/openai need their key set; mock
    always can). Guarantees at least mock, so answer() can never have an empty chain.

    e.g. LLM_PROVIDER_ORDER="claude,openai,mock", only ANTHROPIC_API_KEY set
         → [ClaudeProvider(), MockProvider()]   (openai dropped: no key)
         LLM_PROVIDER_ORDER="claude", no keys
         → [MockProvider()]                      (fallback added)
    """
    providers: list = []
    for name in (n.strip() for n in config.LLM_PROVIDER_ORDER.split(",") if n.strip()):
        if name == "claude" and config.ANTHROPIC_API_KEY:
            from app.llm.claude import ClaudeProvider
            providers.append(ClaudeProvider())
        elif name == "openai" and config.OPENAI_API_KEY:
            from app.llm.openai_provider import OpenAIProvider
            providers.append(OpenAIProvider())
        elif name == "mock":
            providers.append(MockProvider())
    if not providers:
        providers.append(MockProvider())     # always keep a working fallback
    logger.info("llm providers", extra={"order": [p.name for p in providers]})
    return providers


class Gateway:
    def __init__(self) -> None:
        self._providers = _build_providers()
        self._registry = ToolRegistry()

    def answer(self, question: str, principal: Principal) -> tuple[str, str, list[str]]:
        """Return (answer_text, provider_name, tools_used). Fails over across providers.

        e.g. answer("what's my cash balance?", principal)
          → ("Your cash balance is $5.00.", "mock", ["get_balances"])
        Tries providers in order; skips a provider whose breaker is OPEN; a provider
        that raises trips its breaker and we fail over to the next (mock is last).
        """
        for provider in self._providers:
            breaker = get_breaker(provider.name)
            if not breaker.allow():
                continue                       # provider is cooling down → skip
            try:
                text, tools_used = self._run_loop(provider, question, principal)
                breaker.record_success()
                return text, provider.name, tools_used
            except Exception:                  # noqa: BLE001 — any provider error → fail over
                breaker.record_failure()
                logger.exception("provider failed; failing over", extra={"provider": provider.name})
                continue
        raise RuntimeError("all LLM providers failed")

    def _run_loop(self, provider, question: str, principal: Principal) -> tuple[str, list[str]]:
        # returns (answer_text, tools_used), e.g.
        #   ("Your cash balance is $5.00.", ["get_balances"])
        history: list[dict] = [{"role": "user", "text": question}]
        tools_used: list[str] = []
        for _ in range(_MAX_TOOL_ITERATIONS):
            # generate → LLMResult: kind="final" (done), "tools" (run these), or "refusal"
            result = provider.generate(SYSTEM_PROMPT, history, self._registry.specs())
            if result.kind == "refusal":
                return "I can't help with that request.", tools_used
            if result.kind == "final":
                return result.text, tools_used
            # result.kind == "tools": execute each, feed results back, loop.
            history.append({"role": "tool_use", "calls": result.tool_calls})
            results = []
            for call in result.tool_calls:
                tools_used.append(call.name)
                results.append((call.id, self._registry.execute(call.name, call.input, principal)))
            history.append({"role": "tool_result", "results": results})
        return "I couldn't complete that within the tool budget.", tools_used


_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    """Return the process-wide Gateway, building it once on first call (lazy
    singleton). The provider chain and breakers are built once and reused across
    requests, so we don't re-read config or re-instantiate SDK clients per call.

    e.g. get_gateway() is get_gateway()  → True   (same instance every time)
    """
    global _gateway
    if _gateway is None:
        _gateway = Gateway()
    return _gateway
