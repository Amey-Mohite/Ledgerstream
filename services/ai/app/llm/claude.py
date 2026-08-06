"""Claude (Anthropic SDK) provider.

Translates the neutral history to Anthropic's Messages API shape (content blocks:
text / tool_use / tool_result) and back. Uses the default `claude-opus-5` model.
Per the API: no `temperature`/`budget_tokens` on Opus 5, and `stop_reason ==
"refusal"` must be handled before reading content.
"""

from __future__ import annotations

import logging

import anthropic

from app import config
from app.llm.base import LLMResult, ToolCall, ToolSpec

logger = logging.getLogger(__name__)


def _to_claude_messages(history: list[dict]) -> list[dict]:
    """Translate the gateway's neutral history into Anthropic's `messages` shape.

    Neutral roles map to Anthropic like this:
      user        -> {"role":"user",      "content": "<text>"}
      assistant   -> {"role":"assistant", "content": "<text>"}
      tool_use    -> {"role":"assistant", "content": [tool_use blocks]}
      tool_result -> {"role":"user",      "content": [tool_result blocks]}
    (Anthropic quirk: a tool RESULT is sent back under the "user" role.)

    in : [{"role":"user","text":"what's my cash balance?"},
          {"role":"tool_use","calls":[ToolCall("toolu_01","get_balances",{})]},
          {"role":"tool_result","results":[("toolu_01",'[{"code":"CASH","balance":500}]')]}]
    out: [{"role":"user","content":"what's my cash balance?"},
          {"role":"assistant","content":[{"type":"tool_use","id":"toolu_01","name":"get_balances","input":{}}]},
          {"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_01","content":'[{"code":"CASH","balance":500}]'}]}]
    """
    messages: list[dict] = []
    for item in history:
        if item["role"] == "user":
            messages.append({"role": "user", "content": item["text"]})
        elif item["role"] == "assistant":
            messages.append({"role": "assistant", "content": item["text"]})
        elif item["role"] == "tool_use":
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
                for c in item["calls"]
            ]})
        elif item["role"] == "tool_result":
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": out}
                for tid, out in item["results"]
            ]})
    return messages


class ClaudeProvider:
    """Anthropic-backed provider. Speaks the neutral history/LLMResult contract
    so the gateway's one tool-use loop can drive it (or fail over to another)."""

    name = "claude"

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(
            api_key=config.ANTHROPIC_API_KEY, timeout=config.LLM_TIMEOUT
        )

    def generate(self, system: str, history: list[dict], tools: list[ToolSpec]) -> LLMResult:
        """One turn against the Messages API. Maps the response to an LLMResult:
        refusal → kind="refusal"; tool_use blocks → kind="tools"; else the text
        answer → kind="final".

        in : (SYSTEM_PROMPT, [{"role":"user","text":"what's my cash balance?"}], [get_balances, get_transactions])
        out: LLMResult(kind="tools", tool_calls=[ToolCall("toolu_01", "get_balances", {})])
             …then next turn, with the tool_result in history:
             LLMResult(kind="final", text="Your cash balance is $5.00.")
        """
        resp = self._client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=config.LLM_MAX_TOKENS,
            system=system,
            tools=[{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                   for t in tools],
            messages=_to_claude_messages(history),
        )
        logger.info("claude turn", extra={
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "stop_reason": resp.stop_reason,
        })
        if resp.stop_reason == "refusal":
            return LLMResult(kind="refusal")
        calls = [ToolCall(id=b.id, name=b.name, input=b.input)
                 for b in resp.content if b.type == "tool_use"]
        if calls:
            return LLMResult(kind="tools", tool_calls=calls)
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMResult(kind="final", text=text)
