"""OpenAI (Chat Completions) provider — the second LLM behind the gateway.

Same neutral-history contract as the Claude provider; only the wire translation
differs (OpenAI uses `tool_calls` on the assistant message and `role:"tool"`
result messages, with function arguments as a JSON *string*).

(File is `openai_provider.py`, not `openai.py`, so it can't shadow the installed
`openai` package.)
"""

from __future__ import annotations

import json
import logging

import openai

from app import config
from app.llm.base import LLMResult, ToolCall, ToolSpec

logger = logging.getLogger(__name__)


def _to_openai_messages(system: str, history: list[dict]) -> list[dict]:
    """Translate the gateway's neutral history into OpenAI Chat Completions shape.

    Differences from Claude: the system prompt is the FIRST message (not a
    top-level arg), a tool call rides on the assistant message under
    `tool_calls` with arguments as a JSON *string*, and each tool RESULT is its
    own `{"role":"tool", "tool_call_id":...}` message (not folded into "user").

    in : ("You are a ledger assistant.",
          [{"role":"user","text":"what's my cash balance?"},
           {"role":"tool_use","calls":[ToolCall("call_1","get_balances",{})]},
           {"role":"tool_result","results":[("call_1",'[{"code":"CASH","balance":500}]')]}])
    out: [{"role":"system","content":"You are a ledger assistant."},
          {"role":"user","content":"what's my cash balance?"},
          {"role":"assistant","content":None,"tool_calls":[
              {"id":"call_1","type":"function","function":{"name":"get_balances","arguments":"{}"}}]},
          {"role":"tool","tool_call_id":"call_1","content":'[{"code":"CASH","balance":500}]'}]
    """
    messages: list[dict] = [{"role": "system", "content": system}]
    for item in history:
        if item["role"] == "user":
            messages.append({"role": "user", "content": item["text"]})
        elif item["role"] == "assistant":
            messages.append({"role": "assistant", "content": item["text"]})
        elif item["role"] == "tool_use":
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.input)}}
                for c in item["calls"]
            ]})
        elif item["role"] == "tool_result":
            for tid, out in item["results"]:
                messages.append({"role": "tool", "tool_call_id": tid, "content": out})
    return messages


class OpenAIProvider:
    """OpenAI-backed provider. Same neutral history/LLMResult contract as
    ClaudeProvider, so the gateway's tool-use loop drives it identically."""

    name = "openai"

    def __init__(self) -> None:
        self._client = openai.OpenAI(api_key=config.OPENAI_API_KEY, timeout=config.LLM_TIMEOUT)

    def generate(self, system: str, history: list[dict], tools: list[ToolSpec]) -> LLMResult:
        """One turn against Chat Completions. `msg.tool_calls` present →
        kind="tools"; else the message content → kind="final". (OpenAI has no
        distinct refusal stop reason, so only these two kinds come back here.)

        in : (SYSTEM_PROMPT, [{"role":"user","text":"show my recent transactions"}], [get_balances, get_transactions])
        out: LLMResult(kind="tools", tool_calls=[ToolCall("call_abc", "get_transactions", {})])
        """
        resp = self._client.chat.completions.create(
            model=config.OPENAI_MODEL,
            max_tokens=config.LLM_MAX_TOKENS,
            messages=_to_openai_messages(system, history),
            tools=[{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.input_schema}}
                for t in tools],
        )
        msg = resp.choices[0].message
        if resp.usage:
            logger.info("openai turn", extra={
                "input_tokens": resp.usage.prompt_tokens,
                "output_tokens": resp.usage.completion_tokens,
            })
        if msg.tool_calls:
            calls = [ToolCall(id=tc.id, name=tc.function.name,
                              input=json.loads(tc.function.arguments or "{}"))
                     for tc in msg.tool_calls]
            return LLMResult(kind="tools", tool_calls=calls)
        return LLMResult(kind="final", text=msg.content or "")
