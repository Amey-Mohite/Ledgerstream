# Phase 6 — AI Query Service, explained from scratch

> **What Phase 6 adds:** a natural-language layer over a tenant's ledger. `POST
> /api/ai/query {"question": "what's my cash balance?"}` → a plain-English answer,
> **grounded** in that tenant's real data. The first (and only) **FastAPI** service.
>
> Read the three concept docs alongside this — this is the code tour, those are the
> theory: [llm-gateway](concepts/llm-gateway.md),
> [rag-tools-mcp](concepts/rag-tools-mcp.md), [ai-guardrails](concepts/ai-guardrails.md).

The design in one line: the LLM **never writes SQL and never picks a tenant** — it's
given two safe, tenant-scoped read tools, and our code executes them against the Ledger
using the caller's JWT. Safety is enforced by our code, not the model's goodwill.

---

## Part 1 — The end-to-end flow

```mermaid
sequenceDiagram
  autonumber
  participant C as Client
  participant AI as AI service · FastAPI
  participant LLM as LLM · Claude/OpenAI/mock
  participant G as API Gateway
  participant L as Ledger

  C->>AI: POST /api/ai/query {question} (Bearer JWT)
  Note over AI: edge auth (verify JWT) + per-tenant LLM rate limit
  AI->>LLM: question + system prompt + tool specs
  LLM-->>AI: tool_use → get_balances
  AI->>G: GET /api/balances (same JWT)
  G->>L: proxied — edge auth, cache, breaker, tenant-scoped
  L-->>G: balances for this tenant
  G-->>AI: balances for this tenant
  AI->>LLM: tool_result (the balances)
  LLM-->>AI: final: "Your cash balance is $5.00."
  AI-->>C: {answer, provider, tools_used}
```

The model decides *which* tool to call; **our** code runs it — **through the API
Gateway** (`AI → Gateway → Ledger`), tenant-scoped — and feeds the result back; the
model then answers from real data. Routing the tool reads through the gateway (not the
Ledger directly) is deliberate: they inherit the gateway's edge auth, per-tenant rate
limit, balances cache, and circuit breaker.

> **Two different "gateways" — don't conflate them.** The **API Gateway** (Phase 4,
> `:8010`) fronts *services*; the **LLM gateway** (Part 3, inside this service) fronts
> *LLM providers*. The AI service *has* an LLM gateway and *calls* the API Gateway.

---

## Part 1.5 — The flow, function by function (start → end)

The same request as Part 1, but naming the **actual functions** — auth → rate limit →
gateway → tool-use loop → the Gateway hop → back. (`file:function`:
`app/auth.py::require_principal`, `app/guardrails.py::check_rate_limit`,
`app/llm/gateway.py::answer`/`_run_loop`, `provider.generate`,
`app/tools.py::ToolRegistry.execute`/`_gateway_get`.)

```mermaid
sequenceDiagram
  autonumber
  participant C as Client
  participant AI as AI service
  participant LLM as LLM provider
  participant G as API Gateway
  participant L as Ledger

  C->>AI: POST /api/ai/query + Bearer JWT
  Note over AI: require_principal() → jwt.decode(token, KEY)<br/>→ Principal(tenant_id="t-abc", token)
  Note over AI: check_rate_limit("t-abc") — token bucket, 429 if empty
  Note over AI: get_gateway().answer(question, principal)<br/>→ Gateway._run_loop(provider, ...)
  AI->>LLM: provider.generate(system, history, tools)
  LLM-->>AI: kind=tools → get_balances
  Note over AI: registry.execute("get_balances", {}, principal)<br/>allowlist OK → _gateway_get("/api/balances", principal)
  AI->>G: httpx GET /api/balances + Bearer JWT
  G->>L: proxied — tenant-scoped
  L-->>G: balances JSON
  G-->>AI: balances JSON
  Note over AI: history += tool_result → loop again
  AI->>LLM: provider.generate(system, history + result)
  LLM-->>AI: kind=final → "Your cash balance is $5.00"
  Note over AI: breaker.record_success()
  AI-->>C: QueryResponse(answer, provider="mock", tools_used)
```

Read it as one chain: **`require_principal`** (verify JWT → tenant) →
**`check_rate_limit`** (429 if over budget) → **`get_gateway().answer`** →
**`_run_loop`** asks **`provider.generate`**, which returns either a **tool request** or
a **final answer**; on a tool request the loop calls **`ToolRegistry.execute`**
(allowlist) → **`_gateway_get`** (`httpx` → API Gateway → Ledger, tenant-scoped) and
feeds the result back; the model then answers, and the loop returns `(text, provider,
tools_used)`.

---

## Part 1.6 — Deep trace: every function call, with the data at each hop

Parts 1 and 1.5 show *who* calls *whom*. This part is the **call graph** plus a
**line-by-line data trace** — the real values passed in and returned at each step, so
you can read it top to bottom and see exactly what happened. (Trace uses the **mock**
provider so it's fully offline-reproducible; with Claude the final wording is nicer and
`provider="claude"`.)

### The call graph

```mermaid
flowchart TD
  A["POST /api/ai/query<br/>router.query(req, principal)"] --> B["require_principal(authorization)<br/>verify JWT → Principal(tenant_id, token)"]
  B --> C["check_rate_limit(tenant_id)<br/>token bucket → 429 if empty"]
  C --> D["get_gateway().answer(question, principal)"]
  D --> E["_build_providers() — once, cached<br/>keep only keyed providers + mock"]
  D --> F["Gateway._run_loop(provider, question, principal)"]
  F --> G["provider.generate(system, history, tools)"]
  G -->|"kind=tools"| H["ToolRegistry.execute(name, input, principal)"]
  H --> I["allowlist check: is name get_balances or get_transactions?"]
  I --> J["_gateway_get(path, principal)<br/>httpx GET + Bearer JWT"]
  J --> K["API Gateway :8010 → Ledger, tenant-scoped"]
  K --> L["history += tool_result → loop again"]
  L --> G
  G -->|"kind=final"| M["breaker.record_success()<br/>return (text, provider, tools_used)"]
  M --> N["QueryResponse(answer, provider, tools_used)"]
```

### The data trace

**The request:**
```
POST /api/ai/query
Authorization: Bearer eyJhbGci...eyJ0ZW5hbnRfaWQiOiJ0LWFiYyJ9...sig
Body: {"question": "what is my cash balance?"}
```

| # | Function (`file::func`) | Input it receives | What it does | Output it returns |
|---|---|---|---|---|
| 1 | `router.py::query` | `req=QueryRequest(question="what is my cash balance?")`, and FastAPI resolves the `principal` dependency **first** | Entry point. Depends on `require_principal`, so #2 runs before the body | (returns last — see #12) |
| 2 | `auth.py::require_principal` | `authorization="Bearer eyJ...sig"` | Strips `"Bearer "`; `jwt.decode(token, KEY, ["HS256"])` recomputes the HMAC + checks `exp` (no DB). Reads `tenant_id` claim | `Principal(tenant_id="t-abc", token="eyJ...sig")` |
| 3 | `guardrails.py::check_rate_limit` | `"t-abc"` | In-memory token bucket for this tenant: 10 tokens, take 1 → 9 left. (0 left → raise `HTTPException(429)`) | `None` (allowed) |
| 4 | `gateway.py::get_gateway` | — | Lazy singleton: builds the `Gateway` once (provider chain + registry), reuses it after | `Gateway` instance |
| 5 | `gateway.py::_build_providers` | reads `LLM_PROVIDER_ORDER` (say `"mock"` for offline) | Keeps only providers whose key is set; always appends mock | `[MockProvider()]` |
| 6 | `gateway.py::Gateway.answer` | `("what is my cash balance?", Principal(...))` | Picks first provider whose `breaker.allow()` is True; calls `_run_loop`; on success `record_success()` | `("Based on your ledger data: [...]", "mock", ["get_balances"])` |
| 7 | `gateway.py::Gateway._run_loop` | `(MockProvider(), question, principal)` | Seeds `history=[{"role":"user","text":question}]`, then loops (≤4×) | see #8–#11 |
| 8 | `mock.py::MockProvider.generate` **(iter 1)** | `(SYSTEM_PROMPT, history, [get_balances, get_transactions])` | Sees `"balance"` in the question, no tool result yet → asks for the balances tool | `LLMResult(kind="tools", tool_calls=[ToolCall(id="mock-1", name="get_balances", input={})])` |
| 9 | `tools.py::ToolRegistry.execute` | `("get_balances", {}, principal)` | `"get_balances"` is in the allowlist → dispatch to `_gateway_get("/api/balances", principal)` | the balances JSON string (from #10) |
| 10 | `tools.py::_gateway_get` | `("/api/balances", principal)` | `httpx.get("http://localhost:8010/api/balances", headers={Authorization: Bearer eyJ...})` → **API Gateway** validates JWT, cache/breaker, proxies to **Ledger**, tenant-scoped to `t-abc` | `'[{"code":"CASH","balance":500},{"code":"MERCHANT_PAYABLE","balance":-500}]'` |
| 11 | `mock.py::MockProvider.generate` **(iter 2)** | same call, but now `history` has the `tool_result` | Sees the tool result in history → summarizes it, no more tools | `LLMResult(kind="final", text="Based on your ledger data: [{\"code\":\"CASH\",\"balance\":500}, ...]")` |
| 12 | `router.py::query` (returns) | `answer, provider, tools_used` from #6 | Wraps them in the response model | `QueryResponse(answer="Based on your ledger data: ...", provider="mock", tools_used=["get_balances"])` |

**Inside `_run_loop`, `history` grows like this** (this list is the whole "memory" of the turn):
```python
# iter 1 start:
[{"role": "user", "text": "what is my cash balance?"}]
# after the model asks for a tool:
[..., {"role": "tool_use", "calls": [ToolCall("mock-1", "get_balances", {})]}]
# after we run the tool and feed the result back:
[..., {"role": "tool_result", "results": [("mock-1", '[{"code":"CASH","balance":500}, ...]')]}]
# iter 2: model now has the data → returns kind="final" → loop exits
```

**The response:**
```json
{"answer":"Based on your ledger data: [{\"code\":\"CASH\",\"balance\":500}, {\"code\":\"MERCHANT_PAYABLE\",\"balance\":-500}]",
 "provider":"mock","tools_used":["get_balances"]}
```
With a real key, #8/#11 hit Claude instead: same shape, but the final text reads
`"Your cash balance is $5.00."` and `provider="claude"`.

---

## Part 2 — The service shell (Chunk 1)

`services/ai` is the first **FastAPI** service (the locked stack reserves FastAPI for
this one). It's stateless — no database.

- **Edge auth** ([app/auth.py](../services/ai/app/auth.py)) — a FastAPI dependency that
  verifies the Bearer JWT with the shared key (`pyjwt`, same trust model as the other
  services) and returns a `Principal(tenant_id, token)`. It keeps the **raw token** so
  the tools can re-present it to the Ledger.
- **Correlation id** ([app/middleware.py](../services/ai/app/middleware.py)) — the
  Starlette form of the shared correlation primitive.
- **Endpoint** ([app/router.py](../services/ai/app/router.py)) —
  `POST /api/ai/query` → rate-limit guardrail → LLM gateway → answer.

---

## Part 3 — The LLM gateway (Chunk 2)

[app/llm/](../services/ai/app/llm/) — a multi-provider gateway so the app talks to one
interface and the gateway handles model choice, failover, and cost.

- **Neutral interface** (`base.py`) — `Provider.generate(system, history, tools) ->
  LLMResult` where `LLMResult.kind` is `final` / `tools` / `refusal`. The **tool-use
  loop is written once** against this.
- **Providers** — `ClaudeProvider` (Anthropic SDK, default `claude-opus-5`),
  `OpenAIProvider` (OpenAI SDK), and a deterministic `MockProvider` that runs with **no
  keys** (parses the question, drives the same loop, summarizes the tool result). Each
  adapts the neutral history to/from its own wire format (Claude content blocks vs
  OpenAI `tool_calls`).
- **Gateway** (`gateway.py`) — builds the provider chain from `LLM_PROVIDER_ORDER`
  (only providers whose key is set; mock is the always-available fallback), wraps each
  in a **circuit breaker**, and **fails over**: skip an open breaker, try the next on
  error. Logs token usage per turn.

Full theory: [llm-gateway.md](concepts/llm-gateway.md).

---

## Part 4 — Grounding with tools (Chunk 3)

The **tool-use loop** in `gateway.py::_run_loop` is the heart:

```python
history = [{"role": "user", "text": question}]
for _ in range(MAX_ITERS):
    result = provider.generate(SYSTEM_PROMPT, history, registry.specs())
    if result.kind == "final":  return result.text
    # model asked for tools → execute (allowlisted, tenant-scoped), feed back, loop
    history.append({"role": "tool_use", "calls": result.tool_calls})
    history.append({"role": "tool_result",
                    "results": [(c.id, registry.execute(c.name, c.input, principal))
                                for c in result.tool_calls]})
```

The tools ([app/tools.py](../services/ai/app/tools.py)) are exactly two, both read-only:
`get_balances` and `get_transactions`. `execute()` calls the **API Gateway with the
caller's JWT** (`GATEWAY_BASE_URL`, which proxies to the Ledger, tenant-scoped) — so the
model only ever sees its own tenant's data. There is **no `run_sql` tool** and the model
never touches the DB. Full theory: [rag-tools-mcp.md](concepts/rag-tools-mcp.md).

---

## Part 5 — Guardrails (Chunk 4)

Four layers, none of which trust the model ([ai-guardrails.md](concepts/ai-guardrails.md)):

1. **Tool allowlist** (`tools.py`) — `execute` runs only `get_balances`/`get_transactions`;
   any other name returns a harmless error, never execution.
2. **Server-side tenant scoping** — tools carry the caller's JWT; the *Ledger* filters by
   tenant. The model can't cross tenants even if jailbroken.
3. **Prompt-injection defense** (`prompts.py`) — the system prompt forbids inventing
   figures and instructs the model to treat tool output as **data, not instructions**
   (defends against text hidden in fetched data).
4. **Per-tenant LLM rate limit** (`guardrails.py`) — a token bucket caps expensive LLM
   queries → `429`; the tool loop is iteration-bounded.

The mitigating design: the tools are read-only and tenant-scoped, so the worst a
successful injection can do is a read the caller was already entitled to.

---

## Part 6 — The MCP server (Chunk 4)

[app/mcp_server.py](../services/ai/app/mcp_server.py) exposes the **same two tools** over
the **Model Context Protocol** (`FastMCP`), so any MCP client (Claude Desktop, an IDE,
another agent) can call them. The operator supplies a tenant-scoped JWT via
`LEDGERSTREAM_JWT`; every tool forwards it to the Ledger — the same server-side isolation
as the HTTP path.

```bash
LEDGERSTREAM_JWT=<a tenant-scoped access token> python -m app.mcp_server
```

---

## Part 6.5 — Using the MCP server

It's a **stdio** server (`mcp.run()` — FastMCP's default). That means it does **not**
listen on a port: an MCP **client launches it as a subprocess** and talks JSON-RPC over
stdin/stdout. (The HTTP `POST /api/ai/query` path is the *other* way to reach the same two
tools — MCP is the standard-transport way to offer them to an external client.)

**Prerequisites (all three):**
1. `pip install mcp` in the AI service venv (it also needs `httpx` + `ledgerstream_shared`, already present).
2. Gateway (`:8010`) + Ledger up and the tenant **seeded** — the tools call `GATEWAY_BASE_URL`.
3. A tenant-scoped JWT (the server reads it from `LEDGERSTREAM_JWT` and forwards it):
```bash
TOKEN=$(python -c "from dotenv import load_dotenv; load_dotenv(); import os,jwt,time; print(jwt.encode({'tenant_id':'t-demo','user_id':'1','exp':int(time.time())+3600}, os.environ['JWT_SIGNING_KEY'], algorithm='HS256'))")
```

### Option A — MCP Inspector (fastest smoke test)

The official inspector launches the server and gives you a UI to list/call the tools — no
client wiring:
```bash
cd services/ai
LEDGERSTREAM_JWT=$TOKEN npx @modelcontextprotocol/inspector python -m app.mcp_server
```
Open the page it prints → **List Tools** → you'll see `get_balances`/`get_transactions` →
**Run** one → it returns your tenant's ledger JSON.
(PowerShell: `$env:LEDGERSTREAM_JWT=$TOKEN; npx @modelcontextprotocol/inspector python -m app.mcp_server`.)

### Option B — Claude Desktop (real client)

Add to `%APPDATA%\Claude\claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "ledgerstream": {
      "command": "<repo>\\services\\ai\\.venv\\Scripts\\python.exe",
      "args": ["-m", "app.mcp_server"],
      "cwd": "<repo>\\services\\ai",
      "env": { "LEDGERSTREAM_JWT": "PASTE_A_TENANT_TOKEN_HERE" }
    }
  }
}
```
Restart Claude Desktop → the tools appear in the 🔌 menu; ask *"what's my cash balance?"*
and it calls `get_balances` through the gateway. Use the **full venv python path** so
`mcp`/`httpx`/`ledgerstream_shared` resolve, and set `cwd` to `services/ai` so
`app.mcp_server` imports.

### Things to understand

- **stdio, not HTTP** — no port, no `uvicorn`; the client spawns the process and JSON-RPC
  flows over the pipe. This is why it "runs" differently from the FastAPI app.
- **The JWT is the tenant boundary** — whatever tenant `LEDGERSTREAM_JWT` is scoped to is
  all any MCP client can read. The MCP server only *forwards*; the Gateway/Ledger enforce
  isolation — identical to the HTTP path.
- **Token expiry** — the mint above sets `exp` one hour out. When calls start returning
  401, re-mint and update `LEDGERSTREAM_JWT`.

---

## Part 7 — Run it & tests

Run the service (port **8030**). Its tools call the **Gateway** (`:8010`), which proxies
to the Ledger — so the gateway + ledger must be up:

```bash
cd services/ai && uvicorn app.main:app --port 8030
```
```bash
curl -s -X POST http://localhost:8030/api/ai/query -H "Authorization: Bearer $ACCESS" \
  -H "Content-Type: application/json" -d '{"question":"what is my cash balance?"}'
```

With no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, the **mock** provider answers (offline).
With a key set, real Claude/OpenAI answers, grounded via the tools.

**Tests (9, hermetic — mock provider + monkeypatched Ledger, no keys/network):** edge
auth (401/403), the tool-use loop grounds the answer in ledger data, off-topic question
uses no tools, the **allowlist** blocks unknown tools, the **rate limit** returns 429,
and the gateway **fails over** from a broken provider to the mock.

---

## Part 8 — ⚠️ Scaffolded — be ready to explain

- **Why the model never writes SQL / picks the tenant.** Least privilege: two read tools,
  tenant scoping enforced by the Ledger — safety in our code, not the model's.
- **Provider-neutral tool-use loop.** One loop drives Claude or OpenAI because both adapt
  the same neutral history + tool specs.
- **Failover + breaker on the LLM.** LLM calls are slow and providers go down; fail fast
  and fall over to a working provider (or the mock).
- **Indirect prompt injection.** Adversarial text can arrive via *fetched data*, not just
  the user's message; treat tool output as data.
- **Tool use vs MCP.** Tool use is the pattern; MCP is a standard transport/schema to
  offer the same tools to any external client.

---

## Part 9 — Mini-glossary (new terms this phase)

| Term | Meaning |
|---|---|
| **Grounding** | Connecting an LLM to a source of truth (tools/retrieval) so it answers from real data, not hallucination. |
| **Tool use / function calling** | The model requests a named function with JSON inputs; your code runs it and returns the result. |
| **Tool-use loop** | model → (maybe) tool call → execute → feed result back → repeat until a final answer. |
| **LLM gateway** | A layer that fronts multiple LLM providers: one interface, failover, timeouts/breaker, token/cost accounting. |
| **RAG** | Retrieval-Augmented Generation — retrieve relevant documents into the prompt to ground the answer. |
| **MCP** | Model Context Protocol — an open standard for exposing tools/resources to any MCP client. |
| **Prompt injection** | Adversarial text (in the question or fetched data) that tries to override the model's instructions. |
| **Guardrails** | The controls around an LLM (allowlist, scoping, injection defense, rate/cost limits) enforced by your code. |

---

## Part 10 — Questions answered (the ones I got stuck on)

Real questions that came up reading this code, with the answers — so future-me doesn't
re-ask them.

### Q1. Is `kind="final"` a default? Where does it come from?

**No — `kind` has no default.** In `base.py`:
```python
@dataclass
class LLMResult:
    kind: str                                    # REQUIRED — no "= ..."
    text: str = ""                               # these two default
    tool_calls: list[ToolCall] = field(default_factory=list)
```
Only `text` and `tool_calls` default. Every provider **must** set `kind` explicitly, and
it's **computed from the model's response**, not defaulted:
- Claude ([claude.py](../services/ai/app/llm/claude.py)): `stop_reason=="refusal"` →
  `"refusal"`; tool_use blocks present → `"tools"`; else → `"final"`.
- OpenAI ([openai_provider.py](../services/ai/app/llm/openai_provider.py)):
  `msg.tool_calls` present → `"tools"`; else → `"final"`.
- Mock ([mock.py](../services/ai/app/llm/mock.py)): keyword logic.

So `kind="final"` **means "the model gave a plain text answer — no tool needed, the loop
is done."** The comment `# generate → kind="final" | "tools" | "refusal"` just lists the
three *possible* return values; it isn't saying `"final"` is the default.

### Q2. Does the model automatically return `id`, `name`, `input` for a tool call?

**Yes.** When the model decides to use a tool, the API returns a `tool_use` **content
block** with all three fields populated automatically:
```python
# resp.content contains, e.g.:
ToolUseBlock(type="tool_use", id="toolu_01A09q90qw", name="get_balances", input={})
```
- **`id`** — generated by Anthropic (not you). You must echo it back in the `tool_result`
  (`tool_use_id`) so the model matches your result to *this* request.
- **`name`** — the model picks one of the tool names **you** offered in `tools=[...]`. If a
  jailbreak makes it request anything else, the allowlist in
  [tools.py](../services/ai/app/tools.py) rejects it.
- **`input`** — the model fills this to match your `input_schema` (here `{}`, since both
  tools take no params).

That's why the mapping filters blocks:
```python
calls = [ToolCall(id=b.id, name=b.name, input=b.input)
         for b in resp.content if b.type == "tool_use"]   # skip "text" blocks
```
One response can mix `text` and `tool_use` blocks; we keep the `tool_use` ones and map
each into our neutral `ToolCall`.

### Q3. In `_to_claude_messages`, when does the `role == "assistant"` branch ever run?

**In the current code, it doesn't — it's for multi-turn, and we're single-turn today.**
`_run_loop` only ever appends `user`, `tool_use`, and `tool_result` to `history`; when the
model returns a final answer the loop `return`s immediately and **never** appends
`{"role":"assistant","text":...}`. So within one `answer()` call, the `assistant`-text
branch is never reached.

It *would* fire if you added conversation history — replaying a previous answer back in:
```python
history = [
    {"role": "user",      "text": "what's my cash balance?"},
    {"role": "assistant", "text": "Your cash balance is $5.00."},   # ← prior answer replayed
    {"role": "user",      "text": "and my transactions?"},          # follow-up
]
```
Then the branch translates that replayed answer to
`{"role":"assistant","content":"Your cash balance is $5.00."}` so Claude sees the
conversation so far. It's **forward-looking (Tier 3)** — cheap insurance so the translator
handles a full multi-turn history the day we add one, in all three providers at once.
`tool_use`/`tool_result` fire *within* a turn; `assistant` only fires *across* turns.

### Q4. Why did the providers have no docstrings?

They do now — every function in `llm/` carries a one-line purpose + an `in:/out:` example
(the `generate` methods, both `_to_*_messages` translators, `_build_providers`,
`get_gateway`, and the breaker methods). If you spot one without, it's a miss — tell me.

---

## Part 11 — Test it with cURL / the smoke script

The endpoint is `POST /api/ai/query` on **:8030**, body `{"question": "..."}`, needing a
`Bearer` JWT with a `tenant_id` claim (HS256, signed with `JWT_SIGNING_KEY`). Two steps:
mint a token, then call.

**1. Mint a test token** (reads the key from `.env`, never prints the secret):
```bash
TOKEN=$(python -c "from dotenv import load_dotenv; load_dotenv(); import os,jwt,time; print(jwt.encode({'tenant_id':'t-demo','user_id':'1','exp':int(time.time())+3600}, os.environ['JWT_SIGNING_KEY'], algorithm='HS256'))")
```

**2a. Offline test — AI service + auth only** (force mock, ask a non-ledger question so
nothing downstream is needed):
```bash
LLM_PROVIDER_ORDER=mock curl -s -X POST http://localhost:8030/api/ai/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"hello, what can you do?"}'
# → {"answer":"I can answer questions about your account balances and transaction history.","provider":"mock","tools_used":[]}
```

**2b. Full test — grounded answer** (needs Gateway + Ledger up and the tenant seeded, so
the `get_balances` tool has data):
```bash
curl -s -X POST http://localhost:8030/api/ai/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"what is my cash balance?"}'
# → {"answer":"Your cash balance is $5.00.","provider":"claude","tools_used":["get_balances"]}
```

**Status codes:** `401` bad/missing token · `403` token has no `tenant_id` · `429` per-tenant
LLM rate limit (10 burst, ~12/min).

**Or run the whole flow** with [scripts/smoke_ai.sh](../scripts/smoke_ai.sh) — health →
login → non-ledger question → balance question → history question → 401 guard → rate-limit
burst:
```bash
bash scripts/smoke_ai.sh
```
