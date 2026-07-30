# Jaeger — a deep dive (distributed tracing)

> **In one sentence:** Jaeger is an open-source distributed-tracing backend and UI
> that collects the **spans** emitted by your services, stitches them into
> end-to-end **traces**, and lets you *visually* follow one request across every
> service it touched and see exactly where time was spent.

> 🧊 **In plain terms:** when a parcel crosses ten depots and arrives late, you
> don't interrogate each depot separately — you pull up the **tracking timeline**
> that shows the parcel's whole journey, with a bar for how long it sat at each
> stop. Jaeger is that tracking timeline for a *request* moving through your
> services. One glance shows "it spent 900ms waiting in the Ledger service" instead
> of you grepping ten services' logs.

> 🍳 **Where it fits (the kitchen analogy):** Jaeger is the **single-order
> tracking timeline** — for one specific request it shows the journey through every
> station and *where* the time went. [Prometheus](prometheus.md) tells you
> *something's* wrong; Jaeger tells you *where*; the
> [OTel Collector](opentelemetry-collector.md) is the intercom that carried the
> trace here. Full three-tool picture: [Observability §0](observability.md).

> This is the **Jaeger component** deep-dive. For *what a trace/span is
> conceptually* and *why* tracing matters, read [Observability](observability.md).

---

## 1. The problem it solves

In a monolith, a stack trace tells you the whole story of a request. In
**microservices**, one user action fans out across many services, each with its
own logs. No single log has the full picture, and "which service made this slow?"
becomes guesswork.

**Distributed tracing** reconstructs the full journey by having every service
emit **spans** that share a **trace ID**, then assembling them into a tree.
Jaeger is the system that **stores, assembles, and visualizes** those spans.

---

## 2. The data model: trace, span, context

```
 TRACE  (one request; trace_id = abc123) shown as a timeline of spans
 |
 |== Gateway: handle POST /payments ........................ [0ms ---- 120ms]
 |     span_id=1, parent=none (the ROOT span)
 |
 |     == Payment: authorize ............ [10ms -- 45ms]
 |          span_id=2, parent=1
 |          tags: tenant=t1, amount=500, http.status=200
 |
 |     == Payment: publish PaymentCaptured [46ms - 55ms]
 |          span_id=3, parent=1
 |
 |          == Ledger: consume + post entry ..... [60ms --- 110ms]  <- the slow bit
 |               span_id=4, parent=3 (follows-from)
```

- **Span** — one timed unit of work: a name (`Ledger: post entry`), start time,
  duration, a `span_id`, its `parent` span, and **tags/attributes** (key-values
  like `tenant=t1`) plus **events/logs** (timestamped notes within the span).
- **Trace** — the tree of all spans sharing one **`trace_id`**. The first span is
  the **root**.
- **Span context** — the small bundle (`trace_id`, `span_id`, flags) that must be
  **propagated** from service to service so their spans join the same trace.
- **References** — `child-of` (caller waits for callee) and `follows-from`
  (async, fire-and-forget — e.g. a Kafka consumer's span follows the producer's).

---

## 3. Context propagation — how spans in different services join up

The magic is passing the **span context** across the network. When Gateway calls
Payment (or publishes to Kafka), it injects the trace context into the request:

- **W3C Trace Context** — the standard `traceparent` HTTP header:
  `traceparent: 00-<trace_id>-<span_id>-<flags>`. (Also `B3` headers, older/Zipkin.)
- For **Kafka**, the context rides in **message headers**, so the consumer's span
  attaches to the producer's trace via a `follows-from` reference.

The downstream service reads that header, makes its spans **children** of the
incoming span, and forwards the context further. This is exactly why the
correlation/trace ID must be propagated everywhere (see [Observability](observability.md)).

> 🧊 **In plain terms:** each service hands the next one a baton with the race
> number written on it. Everyone runs with the same number, so afterwards you can
> assemble the whole relay from the individually-timed legs.

---

## 4. Jaeger's architecture

Classic Jaeger has several components (worth knowing even though dev uses
"all-in-one"):

```
  services --(OTLP/Jaeger proto)--> [ Collector ] --> [ Storage ] <-- [ Query ] <-- [ UI ]
                                        (validate,       (Cassandra/    (reads      (browser
                                         index, write)    Elasticsearch/  traces)     :16686)
                                                          Badger/memory)
```

- **(Agent)** — historically a per-host daemon that received spans from apps and
  forwarded to the collector. Being phased out in favor of OTLP + the OTel
  Collector.
- **Collector** — receives spans (now commonly via **OTLP**), validates, indexes,
  and writes them to storage. (Not to be confused with the *OTel* Collector,
  though modern Jaeger v2 is literally built on it.)
- **Storage** — pluggable: **Cassandra** or **Elasticsearch** for production,
  **Badger** (local disk) or **in-memory** for dev.
- **Query** — the service that reads traces back out of storage.
- **UI** — the web front end (port **16686**) for searching and visualizing.
- **Spark/dependencies job** — builds the service dependency graph from traces.

**Modern note:** Jaeger v2 is built **on top of the OpenTelemetry Collector**, so
the lines between "OTel Collector" and "Jaeger" are blurring — Jaeger becomes a
Collector distribution with tracing storage + UI.

---

## 5. "all-in-one" (what we run) and its trade-off

`jaegertracing/all-in-one` bundles collector + query + UI + **in-memory storage**
in a single container. Perfect for dev: one image, no external database.

**The trade-off you must know:** in-memory storage means **traces are lost on
restart** and bounded by RAM. That's fine locally, unacceptable in prod — where
you'd use Elasticsearch/Cassandra for durable, searchable, retained traces.

### Our config (there's no `.yaml` — it's docker-compose)

Unlike the OTel Collector and Prometheus, Jaeger all-in-one has **no separate
config file** in our setup — it's configured entirely through the container's
environment and ports in `docker-compose.yml`:

```yaml
jaeger:
  image: jaegertracing/all-in-one:1.60
  environment:
    COLLECTOR_OTLP_ENABLED: "true"   # accept OTLP (from our OTel Collector)
  ports:
    - "16686:16686"                  # the UI, on your laptop at localhost:16686
  healthcheck:
    test: [".../14269/..."]          # admin port used to check it's ready
```

- **`COLLECTOR_OTLP_ENABLED: "true"`** — the single important setting: it turns on
  Jaeger's **OTLP** ingest so it can receive traces on port `4317` *inside the
  Docker network*. Our OTel Collector's `otlp/jaeger` exporter sends to
  `jaeger:4317` (see [OpenTelemetry Collector §6](opentelemetry-collector.md)).
- **`ports: 16686`** — the only port we publish to your laptop: the **UI**. The
  ingest port (`4317`) stays internal — only the Collector talks to it, never your
  browser.
- **Storage** isn't configured because all-in-one defaults to **in-memory** (the
  trade-off above). To make traces durable you'd add `SPAN_STORAGE_TYPE=elasticsearch`
  + connection settings — that's the prod change.

> 🧊 **In plain terms:** Jaeger needs almost no config here — we just flip one
> switch ("accept OTLP") and open one window (the UI). Everything else is defaults.

---

## 6. Sampling (you cannot keep every trace at scale)

Tracing every request at high volume is expensive. Jaeger supports:

- **Head-based sampling** — decide at the *start* of a request whether to trace it
  (e.g. keep 1%). Simple; but you might miss the rare error trace.
- **Remote sampling** — Jaeger can *serve* sampling config to clients centrally, so
  you tune rates without redeploying services.
- **Tail-based sampling** — decide *after* the trace finishes (keep all errors/slow
  ones) — done in the OTel Collector, not Jaeger itself.

Locally we effectively sample everything (low volume). Production picks a strategy
to balance cost vs coverage.

---

## 7. Using the UI (what you'll actually click)

At `http://localhost:16686`:
- **Search** by service, operation, tags (`tenant=t1`), duration (`>500ms`), time
  range — e.g. "find slow Payment traces for tenant t1."
- **Timeline (Gantt) view** — the waterfall of spans; the long bar is your
  bottleneck.
- **Trace comparison** — diff a slow trace against a fast one.
- **Dependency graph** — which services call which, derived from traces.

---

## 8. Alternatives

- **Zipkin** — the older open-source tracer; similar model, simpler.
- **Grafana Tempo** — trace backend that stores in cheap object storage (S3),
  scales cheaply, integrates with Grafana.
- **Vendor APM** — Datadog, Honeycomb, New Relic, Lightstep — managed, richer
  analytics, paid.
All consume OTLP now, so switching is mostly a config change.

---

## 9. Interview questions you should be able to answer

- *What does Jaeger do?* → Collects spans, assembles them into traces by trace_id,
  and visualizes the end-to-end path + latency of a request across services.
- *Span vs trace vs span context?* → Span = one timed unit of work; trace = tree of
  spans sharing a trace_id; span context = the propagated ids that link them.
- *How do spans from different services end up in one trace?* → Context
  propagation — `traceparent` (W3C) headers over HTTP, message headers over Kafka —
  so downstream spans become children/follows-from of the caller's span.
- *child-of vs follows-from?* → Synchronous caller-waits vs asynchronous
  fire-and-forget (e.g. a Kafka consumer span follows the producer span).
- *What's the catch with all-in-one/in-memory storage?* → Traces are lost on
  restart and RAM-bounded; prod uses Elasticsearch/Cassandra.
- *Head vs tail sampling?* → Decide at start (cheap, may miss errors) vs after
  completion (keep errors/slow ones; done in the OTel Collector).
- *How does Jaeger relate to OpenTelemetry?* → Jaeger ingests OTLP; Jaeger v2 is
  built on the OTel Collector. OTel generates/ships; Jaeger stores/visualizes
  traces.

---

## 10. How Ledgerstream uses it

`jaegertracing/all-in-one` runs in Docker with `COLLECTOR_OTLP_ENABLED=true`; the
**OTel Collector** exports our traces to it over OTLP. Spans come from every
service via `ledgerstream_shared.tracing`, and — crucially — the **correlation ID**
is attached to spans, so a single payment flowing Gateway → Payment → Kafka →
Ledger shows up as **one trace** you can open at `http://localhost:16686`, with the
Kafka hop modeled as a `follows-from` reference. In-memory storage is fine for
dev; production would swap in Elasticsearch and a sampling strategy.

---

*Related: [Observability](observability.md) · [OpenTelemetry Collector](opentelemetry-collector.md)
(feeds Jaeger) · [Prometheus](prometheus.md) (metrics counterpart) ·
[`docs/docker-compose-explained.md`](../docker-compose-explained.md).*
