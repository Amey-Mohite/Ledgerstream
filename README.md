# Ledgerstream

A multi-tenant **payment + double-entry ledger** platform with an **AI query
layer**, built as independently deployable backend services communicating over
**Kafka** (event-driven architecture).

Portfolio project demonstrating senior backend + AI system-design for fintech:
outbox pattern, idempotent consumers, saga with compensation, immutable
double-entry ledger, per-tenant isolation, and full observability.

> **Design rationale → [DESIGN.md](DESIGN.md)** — requirements, back-of-envelope
> math, CAP/PACELC per component, partitioning strategy, and an honest
> "load-bearing vs demonstrative" tiering.
>
> **New to this stack? Two learning tracks in [`docs/`](docs/):**
> - **Per-phase walkthroughs** — every file explained from scratch:
>   [phase0](docs/phase0.md) · [phase1](docs/phase1.md) · [phase2](docs/phase2.md) ·
>   [phase3](docs/phase3.md) · [phase4](docs/phase4.md) · [phase5](docs/phase5.md) ·
>   [phase6](docs/phase6.md)
> - **[System-design handbook](docs/concepts/)** — Kafka, outbox, idempotency,
>   double-entry ledger, CAP/PACELC, schema evolution, consumer scaling, auth/JWT,
>   rate limiting, caching, circuit breakers, cursor pagination, load testing, **LLM
>   gateway, RAG/tool-use/MCP, AI guardrails**… written to interview depth with
>   diagrams, code, and plain-English analogies.

---

## Status

| Phase | Scope | State |
|---|---|---|
| **P0** | Foundation: infra stack + shared observability lib | ✅ |
| **P1** | **Payment service** — multi-tenant, JWT, idempotency, outbox, health | ✅ |
| **P2** | **Event backbone** — Avro + Schema Registry, outbox relay → Kafka, **Ledger service** consuming into an immutable double-entry ledger | ✅ |
| **P3** | **Saga hardening** — Ledger emits `LedgerOutcome`, Payment **compensates** rejected payments (→ VOIDED), **retries/backoff + DLQ** on both consumers → **MVP** | ✅ |
| **P4** | **API gateway** (stateless) — edge JWT + reverse proxy, **token-bucket rate limiting**, **Redis cache-aside**, **circuit breaker**, **cursor pagination** | ✅ |
| **P5** | **Proof + seed + load** — invariant/property tests, a `seed` command, and a Locust load test through the gateway | ✅ |
| **P6** | **AI Query service** (FastAPI) — NL→answer over the ledger via a **multi-provider LLM gateway** (Claude/OpenAI/mock + failover), a tenant-scoped **tool-use loop**, guardrails, and an **MCP server** | ✅ |
| P7 | k8s / Helm / Terraform / CI | ⏳ next |

Verified end-to-end against real cloud DBs (Neon) + Kafka: happy path (capture →
outbox → relay → Kafka → consumer → balanced double-entry → balances API) **and**
the saga failure path (GBP payment → ledger rejects → `LedgerOutcome{REJECTED}` →
saga consumer → payment **VOIDED**). The **gateway** fronts it all with edge auth,
rate limiting, caching, and a circuit breaker. Tests: Payment **13**, Ledger **5**,
Gateway **12**, shared **3**.

---

## Architecture (implemented)

```
                    POST /api/auth/token → JWT (carries tenant_id claim)
   Client ─►  API Gateway :8010 (Django, stateless, Redis) ──httpx──► services below
              edge JWT · token-bucket rate limit · balances cache-aside · circuit breaker · cursor history
                                     │  (one JWT works on every hop; correlation-id originates here)
                                     ▼
   Client ─────►  Payment service (Django/DRF)            Ledger service (Django/DRF)
                    authorize → capture                     GET balances / transactions (read)
                          │                                        ▲
                          │ writes outbox row (ONE @atomic tx)     │ post double-entry (idempotent,
                          ▼                                        │  UNIQUE event_id)
                   Outbox Relay ──Avro──► Kafka: payments.events ──► Ledger Consumer
                   (worker, SKIP LOCKED)  + Schema Registry          (worker, consumer group)
                          │                                        │ post journal, OR reject
                 postgres-payment (Neon)                    postgres-ledger (Neon)
                          ▲                                        │
        Saga Consumer ◄──── Kafka: ledger.events ◄──────── LedgerOutcome{POSTED|REJECTED}
        (compensate → VOID on REJECTED)     both consumers: retry+backoff → DLQ on poison

   Cross-cutting: OTel Collector → Jaeger + Prometheus · correlation-id propagated across services
   Auth: shared JWT signing key → Ledger validates statelessly (service-to-service trust)
```

Services run as **native processes** (debuggable); each ships a `Dockerfile`.
Workers (relay, consumer) are **standalone processes**, not in the web cycle.

---

## Quick start

**Prereqs:** Docker + Compose v2, Python 3.11+, and (cloud mode) free accounts on
Neon / Upstash / Atlas — see [`docs/cloud-free-tiers.md`](docs/cloud-free-tiers.md).

```bash
# 1. Start local infra (Kafka + Schema Registry + observability), wait until healthy
docker compose up -d --wait

# 2. Fill .env with your Neon/Upstash/Atlas URLs (copy from .env.example)
cp .env.example .env      # then edit — see docs/cloud-free-tiers.md

# 3. Create the venv + install the three services (Windows paths shown; use .venv/bin on *nix)
python -m venv .venv
.venv/Scripts/pip install -e libs/shared -r services/payment/requirements-dev.txt -r services/ledger/requirements-dev.txt -r services/gateway/requirements-dev.txt -r services/ai/requirements-dev.txt

# 4. Create Kafka topics (once) + migrate the two stateful databases
#    (the gateway is stateless — no database, nothing to migrate)
.venv/Scripts/python infra/kafka/create_topics.py
cd services/payment && ../../.venv/Scripts/python manage.py migrate && cd ../..
cd services/ledger  && ../../.venv/Scripts/python manage.py migrate && cd ../..

# 5. Seed a tenant + user (prints a JWT), from services/payment
cd services/payment && ../../.venv/Scripts/python manage.py create_tenant --name Acme --username acme --password pw && cd ../..
```

Then run the processes, **each in its own terminal** — four API services and three
workers:

```bash
cd services/payment && ../../.venv/Scripts/python manage.py runserver 127.0.0.1:8000     # Payment API
```
```bash
cd services/ledger  && ../../.venv/Scripts/python manage.py runserver 127.0.0.1:8021     # Ledger API
```
```bash
cd services/gateway && ../../.venv/Scripts/python manage.py runserver 127.0.0.1:8010     # API Gateway (public entry)
```
```bash
cd services/ai && ../../.venv/Scripts/python -m uvicorn app.main:app --port 8030          # AI Query (FastAPI); mock LLM if no keys
```
```bash
cd services/payment && ../../.venv/Scripts/python manage.py run_outbox_relay             # Outbox relay (→ Kafka)
```
```bash
cd services/ledger  && ../../.venv/Scripts/python manage.py consume_payments             # Ledger consumer
```
```bash
cd services/payment && ../../.venv/Scripts/python manage.py consume_ledger_outcomes      # Payment saga consumer
```

**Exercise the whole flow through the gateway** (login → create → capture → cache
MISS/HIT → cursor history → saga auto-VOID → rate-limit `429`s):
```bash
bash scripts/smoke_gateway.sh
```

Or hit the services directly, bypassing the gateway:
```bash
bash scripts/smoke.sh
```

**Seed data, then load-test through the gateway** (Phase 5 — run the load step against
full-local infra, not cloud free tiers):
```bash
cd services/payment && ../../.venv/Scripts/python manage.py seed --tenants 5 --payments 100 --capture && cd ../..
```
```bash
pip install -r loadtest/requirements.txt && USERS=5 locust -f loadtest/locustfile.py -H http://localhost:8010
```

---

## The API

| Method | Endpoint | Service | Purpose |
|---|---|---|---|
| POST | `/api/auth/token` | Payment | login (username+password) → `access` + `refresh` JWT |
| POST | `/api/auth/token/refresh` | Payment | new access token from a refresh token |
| POST | `/api/payments` | Payment | authorize a payment (idempotent via `Idempotency-Key` header) |
| GET | `/api/payments` | Payment | list payments (tenant-scoped) |
| GET | `/api/payments/{id}` | Payment | get one |
| POST | `/api/payments/{id}/capture` | Payment | capture → writes the `PaymentCaptured` outbox event |
| POST | `/api/payments/capture` | Payment | **batch** capture (`{payment_ids:[…]}`) → 207 + per-item results |
| GET | `/api/balances` | Ledger | derived account balances (tenant-scoped) |
| GET | `/api/transactions` | Ledger | journal history |
| GET | `/health/live`, `/health/ready` | both | liveness / readiness probes |

**Everything above is also reachable through the API Gateway at `:8010`** (the public
entry point) — which adds edge JWT auth, per-tenant **rate limiting** (`429 + Retry-After`),
balances **cache-aside** (`X-Cache: HIT/MISS`), a **circuit breaker**, and **cursor
pagination** on `/api/transactions`. The same JWT works on every hop (shared signing key).

cURL through the gateway: [`scripts/smoke_gateway.sh`](scripts/smoke_gateway.sh) ·
direct-to-service: [`scripts/smoke.sh`](scripts/smoke.sh) · consumer-group demos:
[`scripts/demo_groups.py`](scripts/demo_groups.py) / [`demo_consumer.py`](scripts/demo_consumer.py).

---

## Run modes (cloud vs fully-local)

Same code, different `.env` — the infra differs:

| Mode | Data stores (Postgres/Redis/Mongo) | Kafka + observability | Start infra |
|---|---|---|---|
| **Cloud** (default) | free cloud tiers (Neon/Upstash/Atlas) | local Docker | `docker compose up -d --wait` |
| **Full-local** (offline) | local Docker | local Docker | `docker compose --profile full up -d --wait` |

Why the split + the Kafka-in-production story:
[`docs/cloud-free-tiers.md`](docs/cloud-free-tiers.md) ·
[`docs/docker-compose-explained.md`](docs/docker-compose-explained.md).

**Infra URLs:** Jaeger http://localhost:16686 · Prometheus http://localhost:9090 ·
Schema Registry http://localhost:8081/subjects · Kafka `localhost:29092`.

---

## Testing

```bash
# tests run against LOCAL Postgres (fast; cloud is for running the app)
docker compose --profile full up -d postgres-payment postgres-ledger
cd services/payment && ../../.venv/Scripts/python -m pytest    # 12 pass
cd services/ledger  && ../../.venv/Scripts/python -m pytest    #  4 pass
```

Proofs: tenant isolation, idempotency-retry, atomic outbox, balanced double-entry,
idempotent consumption, batch partial-success.

---

## Repository layout

```
.
├── docker-compose.yml          # infra stack (health-gated; profiles for cloud/full)
├── Makefile                    # common tasks
├── .env.example                # env template (cloud + local profiles)
├── DESIGN.md                   # design decisions, trade-offs, CAP/PACELC, phase log
├── schemas/avro/               # event contracts (payment_captured.avsc · ledger_outcome.avsc)
├── infra/
│   ├── kafka/create_topics.py  # topic setup (intentional partition counts)
│   ├── otel/ · prometheus/     # observability config
├── libs/shared/                # ledgerstream-shared: logging/tracing/metrics/config/kafka
├── scripts/                    # smoke_gateway.sh · smoke.sh · demo_groups.py · demo_consumer.py
├── loadtest/                   # Locust load test (locustfile.py) — drives the gateway
├── services/
│   ├── payment/                # Django project: config/ core/ tenants/ payments/ outbox/ consumer/ tests/
│   ├── ledger/                 # Django project: config/ core/ ledger/ consumer/ tests/
│   ├── gateway/                # Django project (stateless): config/ core/ gateway/ tests/ — proxy + resilience
│   └── ai/                     # FastAPI (stateless): app/{auth,llm/,tools,guardrails,mcp_server} — NL→ledger
└── docs/                       # phaseN.md walkthroughs + concepts/ handbook
```

---

## Make targets

```
make up          Start infra (cloud mode), wait until healthy
make full-up     Start everything incl. local data stores (offline mode)
make down        Stop (keep data)      make clean   Stop + delete volumes
make health      Show container health make logs    Tail logs
make shared-test Run shared-library unit tests
```
