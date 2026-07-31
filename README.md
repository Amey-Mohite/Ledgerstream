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
>   [phase0](docs/phase0.md) · [phase1](docs/phase1.md) · [phase2](docs/phase2.md)
> - **[System-design handbook](docs/concepts/)** — Kafka, outbox, idempotency,
>   double-entry ledger, CAP/PACELC, schema evolution, consumer scaling… written to
>   interview depth with diagrams, code, and plain-English analogies.

---

## Status

| Phase | Scope | State |
|---|---|---|
| **P0** | Foundation: infra stack + shared observability lib | ✅ |
| **P1** | **Payment service** — multi-tenant, JWT, idempotency, outbox, health | ✅ |
| **P2** | **Event backbone** — Avro + Schema Registry, outbox relay → Kafka, **Ledger service** consuming into an immutable double-entry ledger | ✅ |
| **P3** | Saga hardening — DLQ, retries/backoff, compensation → **MVP** | ⏳ next |
| P4 | Gateway + resilience (rate limit, Redis cache, circuit breaker) | |
| P5 | Proof tests + seed + load test | |
| P6 | AI Query service (RAG/MCP + LLM gateway) | |
| P7 | k8s / Helm / Terraform / CI | |

Verified end-to-end against real cloud DBs (Neon) + Kafka: capture → outbox →
relay (Avro) → Kafka → consumer → balanced double-entry → balances API. Tests:
Payment **12**, Ledger **4**.

---

## Architecture (implemented)

```
                    POST /api/auth/token → JWT (carries tenant_id claim)
   Client ─────►  Payment service (Django/DRF)            Ledger service (Django/DRF)
                    authorize → capture                     GET balances / transactions (read)
                          │                                        ▲
                          │ writes outbox row (ONE @atomic tx)     │ post double-entry (idempotent,
                          ▼                                        │  UNIQUE event_id)
                   Outbox Relay ──Avro──► Kafka: payments.events ──► Ledger Consumer
                   (worker, SKIP LOCKED)  + Schema Registry          (worker, consumer group)
                          │                                        │
                 postgres-payment (Neon)                    postgres-ledger (Neon)

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

# 3. Create the venv + install both services (Windows paths shown; use .venv/bin on *nix)
python -m venv .venv
.venv/Scripts/pip install -e libs/shared -r services/payment/requirements-dev.txt -r services/ledger/requirements-dev.txt

# 4. Create Kafka topics (once) + migrate both databases
.venv/Scripts/python infra/kafka/create_topics.py
cd services/payment && ../../.venv/Scripts/python manage.py migrate && cd ../..
cd services/ledger  && ../../.venv/Scripts/python manage.py migrate && cd ../..

# 5. Seed a tenant + user (prints a JWT), from services/payment
cd services/payment && ../../.venv/Scripts/python manage.py create_tenant --name Acme --username acme --password pw && cd ../..
```

Then run the four processes, **each in its own terminal**:

```bash
cd services/payment && ../../.venv/Scripts/python manage.py runserver 127.0.0.1:8000     # Payment API
```
```bash
cd services/ledger  && ../../.venv/Scripts/python manage.py runserver 127.0.0.1:8021     # Ledger API
```
```bash
cd services/payment && ../../.venv/Scripts/python manage.py run_outbox_relay             # Outbox relay (→ Kafka)
```
```bash
cd services/ledger  && ../../.venv/Scripts/python manage.py consume_payments             # Ledger consumer
```

**Exercise the whole flow** (login → create → capture → ledger):
```bash
bash scripts/smoke.sh
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

The same JWT works on both services (shared signing key). cURL examples:
[`scripts/smoke.sh`](scripts/smoke.sh); consumer-group demos:
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
├── schemas/avro/               # event contracts (payment_captured.avsc)
├── infra/
│   ├── kafka/create_topics.py  # topic setup (intentional partition counts)
│   ├── otel/ · prometheus/     # observability config
├── libs/shared/                # ledgerstream-shared: logging/tracing/metrics/config/kafka
├── scripts/                    # smoke.sh · demo_groups.py · demo_consumer.py
├── services/
│   ├── payment/                # Django project: config/ core/ tenants/ payments/ outbox/ tests/
│   └── ledger/                 # Django project: config/ core/ ledger/ consumer/ tests/
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
