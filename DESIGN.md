# Ledgerstream — Design Document

> A multi-tenant payment + double-entry ledger platform with an AI query layer,
> built as independently deployable services communicating over Kafka.
>
> This document is the single source of truth for **why** the system is shaped
> the way it is. It is updated at the end of every phase. Read it top-to-bottom
> to defend any decision in an interview.

**Status:** Phase 0 complete (infrastructure skeleton + observability + this doc).

---

## Table of contents

1. [Requirements](#1-requirements)
2. [Back-of-envelope estimation](#2-back-of-envelope-estimation)
3. [Architecture overview](#3-architecture-overview)
4. [Per-service responsibilities](#4-per-service-responsibilities)
5. [Event contracts](#5-event-contracts)
6. [Data, consistency & CAP/PACELC](#6-data-consistency--cappacelc)
7. [Partitioning strategy](#7-partitioning-strategy)
8. [Necessity tiering (what's load-bearing vs demonstrative)](#8-necessity-tiering)
9. [Skills checklist](#9-skills-checklist)
10. [Trade-offs & what I'd change at 100× scale](#10-trade-offs--what-id-change-at-100x-scale)
11. [Deliberately left out](#11-deliberately-left-out)
12. [Phase log](#12-phase-log)

---

## 1. Requirements

### Functional
- Multiple tenants (merchants). A tenant owns accounts; every read/write is
  scoped to exactly one tenant.
- Accept a payment intent, **authorize** it, then **capture** it. Each step is
  idempotent under client retry.
- On capture, record the money movement in an **immutable double-entry ledger**
  (every transaction posts balanced debits and credits).
- Expose per-account **balances** and **transaction history** (paginated).
- Natural-language querying of a tenant's own transactions (later phase).

### Non-functional
- **Independent deployability**: each service owns its process, image, and
  database; no shared schema.
- **Event-driven**: services coordinate via Kafka events, not synchronous RPC,
  wherever a workflow tolerates asynchrony.
- **Correctness under failure**: at-least-once event delivery must not double-post
  the ledger; a failed post must not leave a captured-but-unrecorded payment.
- **Multi-tenant isolation**: tenant A can never read or write tenant B's data —
  enforced at the data layer and proven by test.
- **Observability from day one**: structured logs, metrics, and distributed
  traces correlated across services.
- **One-command local run**: `make up` brings up the whole system.

---

## 2. Back-of-envelope estimation

Numbers are illustrative targets that justify the architecture; they are not
what the local demo pushes.

**Assumptions**
- Target peak: **500 payments/sec**, average **200/sec**.
- Each captured payment emits **1 event** and produces **2 ledger rows**
  (one debit + one credit — double entry).
- Ledger row ≈ **256 bytes** (ids, amount, currency, timestamps, references).

**Throughput**
- Ledger write rate at peak: `500 pay/s × 2 rows = 1,000 rows/s`.
- Events on the payments topic at peak: `500 msg/s`.

**Storage growth (ledger, the append-only table)**
- Rows/day at average load: `200 × 2 × 86,400 ≈ 34.6M rows/day`.
- Bytes/day: `34.6M × 256B ≈ 8.85 GB/day` before indexes.
- Over a year: `~3.2 TB` of ledger rows.

**What the math tells us**
- A single Postgres table crossing the **billion-row** mark within a year is
  exactly the regime where table partitioning and archival start to matter →
  see §7. (At demo scale it does not — see §8, this is Tier 2.)
- 500 msg/s is trivial for one Kafka broker; partition count is driven by
  **consumer parallelism and ordering**, not raw throughput → see §7.

**Bandwidth**
- Peak event bandwidth ≈ `500 msg/s × ~1 KB ≈ 0.5 MB/s` — negligible; the
  interesting constraint is ordering/consistency, not pipe size.

---

## 3. Architecture overview

```
                         ┌──────────────────────────┐
        Client  ───────► │  API Gateway / BFF (DRF)  │  JWT · rate limit · corr-id
                         └────────────┬─────────────┘
                    REST     ┌────────┼────────────────┐  REST
                             ▼        ▼                ▼
                 ┌───────────────┐  ┌───────────────┐  ┌────────────────┐
                 │ Payment (DRF) │  │ Ledger (DRF)  │  │ AI Query        │
                 │  + outbox     │  │  read APIs    │  │ (FastAPI)       │
                 └──────┬────────┘  └──────┬────────┘  └───────┬────────┘
                        │ outbox relay      │ consumer          │ scoped reads
                        ▼                   ▲                   ▼
   postgres-payment  ┌──────────────── Kafka (KRaft) ───────────────┐  pgvector
                     │  payments.events  │  ledger.events           │
                     │  *.dlq            │  + Schema Registry (Avro)│
                     └──────────────────────────────────────────────┘
   postgres-ledger ◄─ Ledger consumer         Mongo ◄─ read-model projector
                                                (denormalized tx view, Phase 4)

   Cross-cutting: Redis (cache + rate-limit) · OTel Collector → Jaeger + Prometheus
```

**The saga (Payment → Ledger), the heart of the MVP:**
1. Payment writes the business row **and** an outbox row in one DB transaction
   (`@transaction.atomic`) — this defeats the dual-write problem.
2. An **outbox relay worker** publishes `PaymentCaptured` to Kafka.
3. The **Ledger consumer** idempotently posts a balanced journal entry, then
   emits `LedgerPosted` / `LedgerRejected`.
4. Payment consumes that outcome; on rejection it runs a **compensating action**
   (void / mark-failed). That closes the saga.

---

## 4. Per-service responsibilities

| Service | Stack | Owns | Talks to |
|---|---|---|---|
| **API Gateway / BFF** | Django + DRF | AuthN (JWT), routing, per-key rate limiting, correlation-id origination | All services (REST) |
| **Payment** | Django + DRF | Payment lifecycle, idempotency keys, **outbox**, saga orchestration + compensation | own Postgres; Kafka (produce payments.events, consume ledger.events) |
| **Ledger** | Django + DRF | Immutable double-entry journal, balances, history, read-model projection | own Postgres; Mongo (read view); Kafka (consume payments.events, produce ledger.events) |
| **AI Query** | FastAPI | NL→query over a tenant's ledger, LLM gateway, guardrails | Ledger (scoped reads); LLM providers |

**Why FastAPI for AI Query only:** it is I/O-bound (LLM + retrieval), benefits
from native async/streaming and Pydantic-structured model output, and needs none
of Django's ORM/admin/migration machinery. The other three are transactional and
ORM-heavy, where Django + DRF (auth, throttling, cursor pagination, migrations,
`@transaction.atomic`) pays for itself. Polyglot-by-fit, not by fashion.

---

## 5. Event contracts

Defined in Phase 2 as **Avro** schemas registered in the Schema Registry with
**BACKWARD** compatibility (a new consumer can read old messages). Planned:

- `payments.events` → `PaymentAuthorized`, `PaymentCaptured`, `PaymentVoided`
- `ledger.events` → `LedgerPosted`, `LedgerRejected`

Each event carries: `event_id`, `event_version`, `tenant_id`, `occurred_at`,
`correlation_id`, and a typed payload. Schema evolution rules and the exact
records land here in Phase 2.

---

## 6. Data, consistency & CAP/PACELC

Consistency is chosen **per component**, not globally.

| Component | CAP stance | PACELC | Rationale |
|---|---|---|---|
| **Postgres — Ledger** | **CP** | **PC / EL** | Balances must be correct; under partition we refuse rather than serve a wrong balance. Absent a partition, we still favor consistency (single-primary, synchronous commit) and pay some latency. |
| **Postgres — Payment** | **CP** | **PC / EL** | Idempotency + outbox require the write and its event row to commit atomically; correctness over availability. |
| **Kafka** | **CP** (per partition, with ISR) | **EL** | A partition leader with in-sync replicas prefers consistency; it's the async buffer that lets *read paths* elsewhere be eventual. |
| **Mongo — read model** | **AP** | **EL** | It's a rebuildable projection; serving a slightly stale tenant feed is fine, availability wins. |
| **Redis — cache** | **AP** | **EL** | Cache-aside with a bounded staleness budget; a miss or stale hit is tolerable and cheap to correct. |

**The system-level statement:** *strong* consistency on balances (inside the
Ledger's Postgres), *eventual* consistency on cross-service read views (the Mongo
projection and any cached balance). The trade-off is that a freshly captured
payment may take a beat to appear in the denormalized feed — acceptable, and
called out to the client as read-your-writes-not-guaranteed on those endpoints.

---

## 7. Partitioning strategy

Two distinct things are called "partitioning"; keep them separate.

### 7a. Kafka topic partitioning — **load-bearing**
- **Key = `hash(tenant_id, account_id)`.** Kafka guarantees ordering *within a
  partition*. A ledger needs events for a single account applied in order, so the
  partition key must include the account. Keying on `tenant_id` alone would put a
  whale tenant entirely on one partition (hot partition) and cap its throughput
  at one consumer.
- **Consistent hashing vs naive `modulo`:** with `partition = hash(key) % N`,
  changing `N` (repartitioning to add consumer parallelism) remaps *almost every*
  key to a new partition — catastrophic for a system that relies on per-key
  ordering, because in-flight ordering guarantees break. Consistent hashing remaps
  only `~1/N` of keys when the ring changes, preserving ordering for the rest.
  Documented here because it's the correct mental model even though Kafka's
  default partitioner uses modulo — the lesson is *pick your partition count
  up front and avoid repartitioning*, and where we control the mapping (shard
  routing, cache nodes) we use consistent hashing.

### 7b. Postgres table partitioning — **demonstrative (Tier 2)**
- Ledger entries table partitioned by `HASH(tenant_id)` (or `RANGE` on time).
  Justified by §2's row-growth math **only at scale**. At demo volumes a plain
  indexed table is faster and simpler; partitioning on the wrong key (or too
  early) adds planner overhead and cross-partition query pain. Implemented to
  demonstrate the technique, tagged honestly.
- **Hot-partition risk:** hashing on `tenant_id` still concentrates a whale
  tenant in one partition. Mitigation noted: composite key or sub-partitioning if
  a tenant dominates.

---

## 8. Necessity tiering

Not every listed pattern is equally necessary at this scale. Each is tagged so
nothing in the repo is unexplained cargo-culting.

**Tier 1 — load-bearing (system is wrong/fake without it):**
outbox pattern · idempotent consumers · saga + compensation · double-entry
immutable ledger · multi-tenancy + proven isolation · JWT auth · database-per-service
· migrations · DLQ + retries/backoff · strong-vs-eventual consistency choices ·
health checks · graceful shutdown · structured logs + correlation IDs.

**Tier 2 — real patterns, demonstrative at this scale (built + tagged):**
Kafka partition-count/consistent-hashing reasoning · Postgres table partitioning ·
Mongo read model (a Postgres view would suffice here) · Redis cache-aside ·
token-bucket rate limiting · circuit breakers · Avro schema registry + evolution.
Each becomes load-bearing at a documented scale.

**Tier 3 — production-story showcase (doesn't run locally):**
k8s + Helm + Terraform · CI/CD · load test.

---

## 9. Skills checklist

Event-driven core · consistent hashing · outbox · idempotent consumers ·
rebalancing awareness · DLQ · retries/backoff · Avro schema registry + evolution ·
saga + compensation · double-entry immutable ledger · table partitioning +
hot-partition note · SQL-vs-NoSQL justification · migrations · indexing rationale ·
connection pooling · cursor pagination · consistency choices · Redis cache-aside +
invalidation · token-bucket rate limiting + backpressure · circuit breaker +
graceful degradation · health checks · graceful shutdown · LLM gateway (routing,
caching, cost metering, fallback) · RAG/MCP NL query · OWASP-LLM guardrails ·
JWT/OAuth2 · per-tenant data scoping · service-to-service auth · secrets mgmt ·
tenant-isolation proof · JSON logs · Prometheus metrics · distributed tracing ·
Langfuse AI tracing · docker-compose one-command · Makefile · k8s/Helm/Terraform ·
CI/CD · seed script · load test.

Each is checked off in the phase where it lands (§12).

---

## 10. Trade-offs & what I'd change at 100× scale

_(Grows each phase. Seeds:)_
- **Outbox polling relay → CDC (Debezium).** Polling is simple and correct but
  adds latency and DB load; at scale, stream the outbox via change-data-capture.
- **Single Kafka broker → 3+ brokers, RF=3.** Replication factors are 1 locally;
  production needs `min.insync.replicas=2`, RF=3 for durability.
- **Table partitioning + archival/tiering** once ledger rows pass ~1B (§2).
- **Read-model projector → CQRS with a dedicated stream processor** if read
  shapes multiply.
- **Cloud free tiers → production managed services.** Dev uses Neon/Upstash/Atlas
  free tiers; prod would be RDS/ElastiCache/Atlas paid tiers (or self-hosted on
  k8s) with proper sizing, backups, and multi-AZ. Kafka moves from local Docker to
  MSK/Confluent with SASL_SSL + RF=3/minISR=2/acks=all. All via config, not code.
- **Load testing runs against full-local infra, not cloud free tiers** — cloud
  latency + throttling would swamp the architecture's own numbers, so Phase 5 uses
  `make full-up` (or is documented as methodology + expected figures).

## 11. Deliberately left out

- **Load balancer / CDN / API caching tier** — single-node local; would front the
  gateway in prod. Out of scope for demonstrating service internals.
- **Multi-region / geo-replication** — one region; multi-region consistency is a
  project of its own.
- **Consensus/Raft implementation** — we *use* consensus (Kafka KRaft, Postgres
  replication) but don't hand-roll it; re-implementing Raft demonstrates nothing
  the system needs.
- **Full CQRS** — we do a lightweight read model (Mongo) where it's natural, not a
  full command/query split, which would be over-engineering here.
- **PCI-DSS card-data handling** — payment provider is mocked; we never store PANs.

Each omission is a deliberate scope cut, not an oversight — that distinction is
the point.

---

## 12. Phase log

### Phase 0 — Foundation ✅
**Delivered:** monorepo layout; docker-compose infra stack (Kafka KRaft, Schema
Registry, Postgres ×2, Mongo, Redis, OTel Collector, Jaeger, Prometheus) with
health-gated `make up --wait`; shared observability library
(`ledgerstream-shared`: JSON logging, correlation-id contextvars, OTel tracing,
Prometheus helpers, typed config) with unit tests; this DESIGN.md; top-level
README.

**Skills checked off:** database-per-service topology · docker-compose one-command
· Makefile · structured JSON logs · correlation-id plumbing · Prometheus metrics
scaffolding · distributed tracing scaffolding · schema-registry provisioning.

**Decisions & trade-offs:** see §3, §6. Kafka in KRaft (no ZooKeeper) chosen for a
lighter single-node local cluster that is still *real* Kafka; Avro + Confluent
Schema Registry chosen over JSON Schema for a stronger evolution story at the cost
of two extra containers and binary (less eyeball-friendly) payloads.

**Local development & deployment topology (decided Phase 0):**
- **Services run as native processes**, not containers, during development — for
  a debuggable, instant-restart loop. Each service still ships a `Dockerfile`
  (the production artifact) from Phase 1.
- **Backing data stores run on free cloud tiers** by default (Neon = Postgres ×2,
  Upstash = Redis, MongoDB Atlas = read model). *Why:* makes real production
  concerns concrete — TLS, remote connection strings, credential handling,
  connection pooling, cold starts — instead of faking them against localhost.
- **Kafka + Schema Registry + observability stay in local Docker.** *Why:* Kafka
  has no reliable perpetual free tier (Confluent Cloud is credit-billed), and it's
  the component we iterate on most — local keeps it free and fully controllable.
  The production path (MSK / Confluent / Redpanda / Strimzi, with SASL_SSL and
  RF=3/minISR=2/acks=all) is documented in `docs/cloud-free-tiers.md §5`.
- **Config portability is the payoff:** every backing store is addressed via an
  env var, so cloud↔local↔production is a `.env` change, not a code change
  (12-factor). Compose **profiles** express the two local modes (default = cloud
  data stores + local Kafka; `--profile full` = everything local/offline).
- **Trade-off — database-per-service is *logical*, not *physical*, on Neon free
  tier:** the two databases live in one Neon project (separate DBs + roles, no
  cross-DB queries) rather than two instances. Production would use two separate
  instances/projects. Noted so it's defensible, not accidental.
- **Trade-off — load testing (Phase 5) can't run against cloud free tiers:**
  results would measure internet latency and free-tier throttling, not the
  architecture. Phase 5 runs against **full-local** infra (`make full-up`), or is
  reframed as methodology + expected numbers. Recorded in §10.

**Scaffolded — be ready to explain (may not fully grasp yet):**
- **KRaft mode**: the broker is its own controller via a Raft quorum; replaces
  ZooKeeper. You should be able to say *what* the controller does (metadata,
  leader election) even though we run a single node.
- **OTel Collector fan-out**: services push OTLP once; the collector routes traces
  to Jaeger and metrics to Prometheus. Know *why* a collector sits in the middle
  (decouples services from backends, central batching/sampling).
- **`contextvars` for correlation id**: understand why this (not thread-locals or
  globals) is correct across both Django threads and FastAPI coroutines.
- **Schema Registry BACKWARD compatibility**: know what "backward compatible"
  means (new schema can read data written by old schema) before Phase 2.
