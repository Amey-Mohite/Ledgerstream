"""Deterministic mock provider — runs with no API keys.

It parses the question for keywords, drives the SAME tool-use loop the real
providers do (so the whole pipeline is exercised), and summarizes the tool result.
This keeps the service and its tests runnable offline, and is the always-available
failover at the end of the provider chain.
"""

from __future__ import annotations

from app.llm.base import LLMResult, ToolCall, ToolSpec

_TX_WORDS = ("transaction", "history", "recent", "activity", "statement")


class MockProvider:
    """Deterministic, no-network provider. Same contract as the real ones, so
    the whole pipeline runs offline (tests, no-keys demo) and it's the final
    always-available failover in the provider chain."""

    name = "mock"

    def generate(self, system: str, history: list[dict], tools: list[ToolSpec]) -> LLMResult:
        # "what's my cash balance?" (1st turn) → LLMResult(kind="tools", [get_balances])
        # …after the tool result is in history  → LLMResult(kind="final", "Based on your ledger data: …")
        # "what's the weather?"                  → LLMResult(kind="final", "I can answer about balances/transactions.")
        question = next((h["text"] for h in reversed(history) if h["role"] == "user"), "").lower()
        already_ran_tools = any(h["role"] == "tool_result" for h in history)

        if not already_ran_tools:
            # First turn: decide which read tool answers the question.
            if "balance" in question:
                return LLMResult(kind="tools",
                                 tool_calls=[ToolCall(id="mock-1", name="get_balances", input={})])
            if any(w in question for w in _TX_WORDS):
                return LLMResult(kind="tools",
                                 tool_calls=[ToolCall(id="mock-1", name="get_transactions", input={})])
            return LLMResult(kind="final", text=(
                "I can answer questions about your account balances and transaction history."))

        # Second turn: the tool result is in history — summarize it.
        last = next(h for h in reversed(history) if h["role"] == "tool_result")
        _tool_call_id, output = last["results"][0]
        return LLMResult(kind="final", text=f"Based on your ledger data: {output}")
