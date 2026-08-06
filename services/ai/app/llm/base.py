"""Provider-neutral types for the LLM gateway.

The tool-use loop is written ONCE against these types; each provider (Claude,
OpenAI, mock) translates them to/from its own wire format. That's what lets one
loop drive multiple providers with failover.

The loop keeps a **neutral history** — a list of dicts:
    {"role": "user",        "text": "what's my cash balance?"}
    {"role": "assistant",   "text": "Your cash balance is $5.00."}
    {"role": "tool_use",    "calls": [ToolCall(...), ...]}      # model asked for tools
    {"role": "tool_result", "results": [(tool_call_id, output_str), ...]}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolSpec:
    """A tool offered to the model (name + description + JSON-schema inputs)."""

    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    """The model's request to call a tool."""

    id: str
    name: str
    input: dict


@dataclass
class LLMResult:
    """One provider turn: either final text, a set of tool calls, or a refusal."""

    kind: str                                    # "final" | "tools" | "refusal"
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class Provider(Protocol):
    name: str

    def generate(self, system: str, history: list[dict], tools: list[ToolSpec]) -> LLMResult:
        """Produce the next turn given the system prompt, neutral history, and tools."""
        ...
