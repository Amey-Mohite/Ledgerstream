# System Design Concepts — the handbook

This folder is your **standalone system-design textbook**. Each file explains one
principle **in general** — what it is, the problem it solves, how it works, its
variations and trade-offs, when to use it (and when not), and the interview
questions you should be able to answer. The Ledgerstream-specific usage is a
short note at the end of each; the bulk is knowledge you can carry anywhere.

> **How to use this:** when you hit a term you don't know in `DESIGN.md` or a
> `docs/phaseN.md`, come here and read the matching concept file end to end.

---

## Reading order (foundations first)

> **New to auth?** Read [Authentication, JWTs & Multi-Tenancy](authentication-and-jwt.md)
> first — it explains tokens, the login flow, and what a "tenant" is, from scratch.

If you're starting cold, read in this order — each builds on the previous:

1. [Microservices & Database-per-Service](microservices-and-database-per-service.md)
2. [Event-Driven Architecture & the Message Bus](event-driven-architecture.md)
3. [Apache Kafka — deep dive](kafka.md)
4. [CAP & PACELC](cap-and-pacelc.md)
5. [Consensus & Coordination (Raft, quorums)](consensus-and-coordination.md)
6. [Schema Evolution & Contracts](schema-evolution-and-contracts.md)
7. [Schema Registry — deep dive](schema-registry.md)
8. [Observability (logs, metrics, traces)](observability.md)
9. [OpenTelemetry & the Collector — deep dive](opentelemetry-collector.md)
10. [Jaeger — deep dive (tracing backend)](jaeger.md)
11. [Prometheus — deep dive (metrics)](prometheus.md)
12. [Health Checks, Liveness & Readiness](health-checks-liveness-readiness.md)
13. [The Saga Pattern](saga-pattern.md)
14. [The Outbox Pattern](outbox-pattern.md)  *(Phase 1)*
15. [Idempotency](idempotency.md)  *(Phase 1)*
16. [Multi-Tenancy & Tenant Isolation](multi-tenancy.md)  *(Phase 1)*
17. [The Double-Entry Ledger](double-entry-ledger.md)  *(Phase 2)*
18. [Partitioning & Consistent Hashing](partitioning-and-consistent-hashing.md)  *(Phase 2)*
19. [Dead-Letter Queues, Retries & Backoff](dlq-and-retries.md)  *(Phase 3)*
20. [Building an Internal Shared Library](internal-shared-library.md)  *(Phase 0)*
21. [Rate Limiting & Backpressure](rate-limiting.md)  *(Phase 4)*
22. [Caching & Cache Invalidation](caching-and-invalidation.md)  *(Phase 4)*
23. [Circuit Breakers & Graceful Degradation](circuit-breakers.md)  *(Phase 4)*
24. [Cursor (Keyset) Pagination](cursor-pagination.md)  *(Phase 4)*
25. [Load Testing & Performance](load-testing-and-performance.md)  *(Phase 5)*
26. [The LLM Gateway](llm-gateway.md)  *(Phase 6)*
27. [Grounding: Tool Use, RAG & MCP](rag-tools-mcp.md)  *(Phase 6)*
28. [AI Guardrails](ai-guardrails.md)  *(Phase 6)*

---

## Concept → phase map

| Concept | Deep-dive | Where it shows up | Status |
|---|---|---|---|
| Microservices & database-per-service | [link](microservices-and-database-per-service.md) | whole architecture | ✅ written |
| Event-driven architecture / message bus | [link](event-driven-architecture.md) | Kafka backbone | ✅ written |
| Apache Kafka internals (deep dive) | [link](kafka.md) | Kafka backbone | ✅ written |
| CAP & PACELC | [link](cap-and-pacelc.md) | DESIGN.md §6 | ✅ written |
| Consensus & coordination (Raft/KRaft) | [link](consensus-and-coordination.md) | Kafka KRaft, Postgres | ✅ written |
| Schema evolution & contracts | [link](schema-evolution-and-contracts.md) | Avro + Schema Registry | ✅ written |
| Schema Registry internals (deep dive) | [link](schema-registry.md) | Confluent SR container | ✅ written |
| Observability (3 pillars) | [link](observability.md) | OTel/Jaeger/Prometheus | ✅ written |
| OpenTelemetry & the Collector (deep dive) | [link](opentelemetry-collector.md) | otel-collector container | ✅ written |
| Jaeger — tracing backend (deep dive) | [link](jaeger.md) | jaeger container | ✅ written |
| Prometheus — metrics (deep dive) | [link](prometheus.md) | prometheus container | ✅ written |
| Health checks (liveness/readiness) | [link](health-checks-liveness-readiness.md) | compose healthchecks | ✅ written |
| Saga pattern | [link](saga-pattern.md) | Payment→Ledger flow | ✅ written (you asked!) |
| Outbox pattern | [link](outbox-pattern.md) | Payment producer | ✅ written |
| Idempotency | [link](idempotency.md) | payments + consumers | ✅ written |
| Partitioning & consistent hashing | [link](partitioning-and-consistent-hashing.md) | Kafka keys, DB sharding | ✅ written |
| Dead-letter queues & retries/backoff | [link](dlq-and-retries.md) | consumer resilience | ✅ written |
| Internal shared library / Python packaging | [link](internal-shared-library.md) | `libs/shared` | ✅ written |
| Caching & cache invalidation | [link](caching-and-invalidation.md) | gateway Redis cache-aside | ✅ written |
| Rate limiting & backpressure | [link](rate-limiting.md) | gateway token bucket | ✅ written |
| Circuit breakers & graceful degradation | [link](circuit-breakers.md) | gateway → backends | ✅ written |
| Cursor pagination | [link](cursor-pagination.md) | ledger history API | ✅ written |
| Load testing & performance | [link](load-testing-and-performance.md) | Locust + seed + proofs | ✅ written |
| Double-entry ledger / event sourcing ideas | [link](double-entry-ledger.md) | ledger core | ✅ written |
| Authentication, JWTs & tenancy | [link](authentication-and-jwt.md) | login + every request | ✅ written |
| Multi-tenancy & isolation | [link](multi-tenancy.md) | every data access | ✅ written |
| LLM gateway (multi-provider) | [link](llm-gateway.md) | AI Query service | ✅ written |
| Grounding: tool use / RAG / MCP | [link](rag-tools-mcp.md) | AI Query tools + MCP | ✅ written |
| AI guardrails | [link](ai-guardrails.md) | AI Query service | ✅ written |

**Forward references you may hit early in DESIGN.md** (teasers until their full
file lands):
- **Outbox pattern** — how to update a database *and* publish an event without
  them getting out of sync. One-liner: write the event into your own DB in the
  same transaction as the business change, then a separate process ships it to
  the message bus.
- **Idempotency** — making an operation safe to run more than once with the same
  result (so retries don't double-charge).
- **Consistent hashing** — a way to map keys to nodes/partitions so that adding
  or removing a node moves *few* keys, not all of them.
