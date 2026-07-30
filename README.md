# Ledgerstream

A multi-tenant **payment + double-entry ledger** platform with an **AI query
layer**, built as independently deployable backend services communicating over
**Kafka** (event-driven architecture).

Portfolio project demonstrating senior backend + AI system-design for fintech:
outbox pattern, idempotent consumers, saga with compensation, immutable
double-entry ledger, per-tenant isolation, and full observability — all running
locally via `docker-compose`.

> **Design rationale lives in [DESIGN.md](DESIGN.md)** — requirements,
> back-of-envelope math, CAP/PACELC per component, partitioning strategy, and an
> honest "what's load-bearing vs demonstrative" tiering.
>
> **New to this stack? Two learning tracks in [`docs/`](docs/):**
> - [`docs/phase0.md`](docs/phase0.md) — per-phase walkthrough of **every file**,
>   from scratch.
> - [`docs/concepts/`](docs/concepts/) — a standalone **system-design handbook**
>   (event-driven architecture, CAP/PACELC, consensus, schema evolution,
>   observability, sagas…), written to interview depth with diagrams and plain-
>   English analogies.

---

## Status

**Phase 0 — Foundation (complete):** infrastructure skeleton + shared
observability library. No business logic yet; services land in later phases.

Roadmap: **P1** Payment core + outbox · **P2** Kafka backbone + Ledger consumer +
Avro contracts · **P3** saga hardening (DLQ, retries, compensation) → *MVP* ·
**P4** gateway + resilience (rate limit, cache, circuit breaker) · **P5** proof
tests + seed + load test · **P6** AI Query service · **P7** k8s/Terraform/CI.

---

## Architecture (current)

```
API Gateway (DRF) ──► Payment (DRF) ──outbox──► Kafka (KRaft) ──► Ledger (DRF)
                 └──► Ledger reads             + Schema Registry   │
                 └──► AI Query (FastAPI)          (Avro)           ▼
                                                            postgres-ledger
 Cross-cutting: Postgres×2 · Mongo · Redis · OTel Collector → Jaeger + Prometheus
```

Phase 0 ships the backing stack (everything below the service boxes) plus the
shared library the services will use.

---

## Requirements

- Docker + Docker Compose v2 (`docker compose version`) — for Kafka + observability
- Python 3.11+ — services run as native processes; also runs the shared-lib tests
- For **cloud mode** (default): free accounts on Neon, Upstash, MongoDB Atlas
  — see [`docs/cloud-free-tiers.md`](docs/cloud-free-tiers.md)

---

## Run modes

Services run as **native local processes** (debuggable, instant restart). Only the
backing infrastructure differs between two modes — same code, different `.env`:

| Mode | Data stores (Postgres/Redis/Mongo) | Kafka + observability | Command |
|---|---|---|---|
| **Cloud** (default, daily dev) | free cloud tiers (Neon/Upstash/Atlas) | local Docker | `make up` |
| **Full local** (offline demo) | local Docker | local Docker | `make full-up` |

### Cloud mode (recommended)

1. Create the free accounts and copy the connection strings into `.env` —
   full walkthrough in [`docs/cloud-free-tiers.md`](docs/cloud-free-tiers.md).
2. Start local Kafka + observability:
   ```bash
   make up
   ```
   (or without `make`: `docker compose up -d --wait`)

This starts **Kafka + Schema Registry + OTel Collector + Jaeger + Prometheus**
and blocks until healthy. The local Postgres/Mongo/Redis stay *off* (they're in
the `full` profile).

### Full-local mode (no cloud accounts needed)

```bash
make full-up          # docker compose --profile full up -d --wait
```

Starts everything in Docker. Point `.env` at the `LOCAL` alternatives (commented
in `.env.example`).

> Details on why the split, and what Kafka looks like in production, are in
> [`docs/cloud-free-tiers.md`](docs/cloud-free-tiers.md) and
> [`docs/docker-compose-explained.md`](docs/docker-compose-explained.md).

### Verify it's up

```bash
make health
```

| Service | URL / host |
|---|---|
| Jaeger (traces) | http://localhost:16686 |
| Prometheus (metrics) | http://localhost:9090 |
| Schema Registry | http://localhost:8081/subjects |
| Kafka (host bootstrap) | `localhost:29092` |
| Postgres (payment) — *full mode* | `localhost:5433` |
| Postgres (ledger) — *full mode* | `localhost:5434` |
| Mongo — *full mode* | `localhost:27017` |
| Redis — *full mode* | `localhost:6379` |

### Shared library tests

```bash
make shared-install
make shared-test
```

### Tear down

```bash
make down     # stop, keep data
make clean    # stop and DELETE all volumes (destructive)
```

---

## Repository layout

```
.
├── docker-compose.yml      # infra stack (health-gated)
├── Makefile                # common tasks (wrappers over docker compose)
├── .env.example            # environment template (copy to .env)
├── DESIGN.md               # design decisions, trade-offs, CAP/PACELC
├── infra/
│   ├── otel/               # OpenTelemetry Collector config
│   └── prometheus/         # Prometheus scrape config
├── libs/
│   └── shared/             # ledgerstream-shared: logging/tracing/metrics/config
└── services/               # payment · ledger · gateway · ai-query (later phases)
```

---

## Make targets

```
make help            List all targets
make up              Start stack, wait until healthy
make down            Stop stack (keep data)
make health          Show container health
make logs            Tail all logs
make shared-test     Run shared-library unit tests
make clean           Stop and delete volumes (destructive)
```
