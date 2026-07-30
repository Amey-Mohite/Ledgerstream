# OpenTelemetry & the Collector — a deep dive

> **In one sentence:** OpenTelemetry (OTel) is the vendor-neutral standard for
> *generating* telemetry (traces, metrics, logs), and the **Collector** is a
> standalone pipeline process that *receives* that telemetry, *processes* it, and
> *routes* it to whatever backends you use — so your application code is
> instrumented once and never coupled to a specific monitoring vendor.

> 🧊 **In plain terms:** imagine every worker in a factory jotting notes about what
> they're doing. Without a system, each worker would have to know the address of
> the filing office, the accountant, and the safety inspector, and mail copies to
> each. The **Collector** is the mailroom: workers drop every note in one inbox,
> and the mailroom sorts, batches, redacts, and forwards each to the right
> department. Change the accountant's address? You update the mailroom, not all
> 500 workers.

> 🍳 **Where it fits (the kitchen analogy):** the Collector is the **intercom &
> mailroom** — every service reports telemetry in one shared language (**OTLP**)
> into one place, and it routes each kind to the right tool: metrics →
> [Prometheus](prometheus.md), traces → [Jaeger](jaeger.md). It's the *plumbing*
> that carries everything the other two display. Full three-tool picture:
> [Observability §0](observability.md).

> This is the **OTel + Collector** deep-dive. For the *concepts* of the three
> telemetry pillars (what a trace/metric/log each is, percentiles, correlation
> IDs) read [Observability](observability.md) first; this file is about the
> **standard and the routing component**.

---

## 1. What "OpenTelemetry" actually is

OTel is not a tool you run — it's a **specification + libraries**. It has parts:

- **API** — the interface your code calls to create spans/metrics (`tracer.start_span(...)`).
- **SDK** — the implementation behind the API (sampling, batching, exporting).
- **OTLP** (OpenTelemetry Protocol) — the wire format/protocol telemetry is sent in
  (gRPC on `:4317`, HTTP on `:4318`). **This is the lingua franca** everything
  speaks.
- **Semantic conventions** — agreed names for common attributes (`http.method`,
  `service.name`) so data from different systems is comparable.
- **The Collector** — the optional (but recommended) routing process this doc is
  about.

**Why it matters:** before OTel, each vendor (Datadog, New Relic, Jaeger) had its
own agent and SDK. Instrument for one, you were locked in. OTel decouples
*generating* telemetry from *storing/viewing* it. Instrument once against OTel →
send anywhere.

---

## 2. Why put a Collector in the middle at all?

Your app's SDK *could* export straight to a backend (e.g. directly to Jaeger). The
Collector is a deliberate extra hop that buys you a lot:

- **Decoupling** — services only know "send OTLP to the collector." Swap Jaeger
  for Tempo, or add Prometheus, by editing **one** collector config, not every
  service.
- **Central processing** — batch, sample, filter, redact PII, and add common
  attributes in one place instead of in every service.
- **Protocol translation** — receive OTLP, export to a backend that speaks
  something else. It's a universal adapter.
- **Buffering & resilience** — retry/queue when a backend is briefly down, so app
  threads aren't blocked and data isn't dropped.
- **Offloading** — keep the heavy lifting out of your latency-sensitive app
  process.

> 🧊 **In plain terms:** the Collector is a power adapter + surge protector between
> your appliances (services) and the wall sockets (backends). New socket shape?
> Change the adapter, not the appliances.

---

## 3. The Collector's internal architecture: pipelines

A Collector is built from four component types wired into **pipelines**:

```
          RECEIVERS         PROCESSORS            EXPORTERS
        (data comes in)   (transform/batch)    (data goes out)

  OTLP ---> [otlp] ---> [memory_limiter] ---> [batch] ---> [otlp/jaeger] ---> Jaeger
                                                      \--> [prometheus]   ---> (scraped)
                                                      \--> [debug]        ---> stdout

        \________________________ one PIPELINE ________________________/
             (a pipeline is per-signal: traces | metrics | logs)
```

- **Receivers** — how telemetry gets *in*. `otlp` is the main one (gRPC 4317 / HTTP
  4318). Others exist (`prometheus`, `jaeger`, `zipkin`, `filelog`, `kafka`…) —
  the Collector can even *scrape* Prometheus targets and forward them.
- **Processors** — transform data as it flows. Common ones:
  - `batch` — group telemetry to export efficiently (fewer, bigger calls).
  - `memory_limiter` — drop data before the Collector OOMs under load.
  - `attributes` / `resource` — add/edit/remove attributes (e.g. redact PII).
  - `tail_sampling` — decide *after* a trace completes whether to keep it (so you
    can keep all errors/slow traces) — a gateway-Collector feature.
  - `filter` — drop noisy signals.
- **Exporters** — how telemetry goes *out*. `otlp` (to another OTLP endpoint like
  Jaeger), `prometheus` (expose metrics for Prometheus to scrape), `debug` (print
  to console), plus vendor exporters (Datadog, etc.).
- **Extensions** — non-pipeline add-ons like `health_check` (an HTTP liveness
  endpoint) or `pprof`.

**A pipeline is per-signal:** you define a `traces` pipeline, a `metrics`
pipeline, and/or a `logs` pipeline, each a receivers→processors→exporters chain.

---

## 4. Two deployment topologies (interview-relevant)

```
  AGENT mode (one Collector per host/pod — a sidecar/daemonset)
    app --> local collector (agent) --> ... --> backend
    + low-latency local hop, adds host metadata, offloads the app

  GATEWAY mode (a standalone Collector cluster many services share)
    many agents/apps --> [ gateway collector cluster ] --> backends
    + central place for tail-sampling, rate control, egress auth

  Common production setup = BOTH: agent on each node -> central gateway -> backends
```

- **Agent** — runs next to the app (sidecar container, or a daemon per node).
  Cheap local hop, enriches with host info.
- **Gateway** — a shared, horizontally-scaled Collector deployment. The right place
  for **tail-based sampling** (needs to see whole traces) and centralized egress.

Our local stack runs a **single gateway-style Collector** — simplest for dev.

---

## 5. Core vs Contrib distributions

The Collector ships in two builds:
- **Core** — the OTel-maintained, minimal set of components.
- **Contrib** (`otel/opentelemetry-collector-contrib`) — core **plus** a large set
  of community receivers/processors/exporters (vendor exporters, extra receivers).
We use **contrib** because it includes the exporters we need (Prometheus, etc.).
In production you'd often build a **custom distribution** with only the components
you use (smaller, smaller attack surface) via the OpenTelemetry Collector Builder.

---

## 6. Our config file, explained line by line

This is the **actual file** `infra/otel/otel-collector-config.yaml`. It's just the
four component types from §3 (receivers, processors, exporters, extensions) plus a
`service:` block that wires them into pipelines.

```yaml
receivers:
  otlp:                          # ONE receiver: accept OpenTelemetry data
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317   # services push traces/metrics here (gRPC)
      http:
        endpoint: 0.0.0.0:4318   # ...or here (HTTP), whichever the SDK uses

processors:
  batch:                         # group data into batches before exporting
    timeout: 1s                  # ...but never wait more than 1s
    send_batch_size: 1024        # ...or once 1024 items have piled up

exporters:
  otlp/jaeger:                   # send TRACES onward to Jaeger
    endpoint: jaeger:4317        # Jaeger's OTLP port (Docker DNS name "jaeger")
    tls:
      insecure: true             # no TLS locally (prod would encrypt)
  prometheus:                    # expose METRICS for Prometheus to scrape
    endpoint: 0.0.0.0:8889       # Prometheus pulls from otel-collector:8889
  debug:                         # also print to the console (handy while learning)
    verbosity: normal

extensions:
  health_check:
    endpoint: 0.0.0.0:13133      # a /health endpoint the Docker healthcheck curls

service:                         # THE WIRING — nothing runs unless listed here
  extensions: [health_check]
  pipelines:
    traces:                      # pipeline 1: traces
      receivers:  [otlp]         #   in  ← from services
      processors: [batch]        #   batch them
      exporters:  [otlp/jaeger, debug]   # out → Jaeger (+ console echo)
    metrics:                     # pipeline 2: metrics
      receivers:  [otlp]         #   in  ← from services
      processors: [batch]
      exporters:  [prometheus]   # out → re-exposed on :8889 for Prometheus
```

**Reading it top to bottom:**
- **`receivers.otlp`** — the single front door. Services send OTLP on `4317`
  (gRPC) or `4318` (HTTP). `0.0.0.0` = "listen on all interfaces in the container."
- **`processors.batch`** — bundles telemetry so we make few, larger export calls
  instead of one per span/metric (efficiency). `timeout`/`send_batch_size` cap how
  long/large a batch gets.
- **`exporters`** — three outputs: `otlp/jaeger` (traces → Jaeger), `prometheus`
  (metrics re-exposed on `:8889`), `debug` (console echo, learning aid).
- **`extensions.health_check`** — a tiny HTTP health endpoint on `:13133`; the
  container's Docker healthcheck curls it to decide the Collector is ready.
- **`service.pipelines`** — the crucial part: a component only runs if it's wired
  into a pipeline here. We define **two** (per-signal): a `traces` pipeline and a
  `metrics` pipeline, each `receivers → processors → exporters`.

> **The model flip to notice:** traces are **pushed out** to Jaeger, but metrics
> are **pulled** — the Collector just *exposes* them on `:8889` and Prometheus
> scrapes that. Same Collector, two directions. (Why Prometheus pulls:
> [Prometheus §1](prometheus.md).)

> 🧊 **In plain terms:** the file says "accept mail at these two doors (receivers),
> bundle it (processor), and send trace-mail to Jaeger while putting metric-mail on
> a shelf Prometheus comes to collect (exporters) — and here's the routing slip
> (pipelines) that makes it actually happen."

---

## 7. Production concerns

- **`memory_limiter` first** in every pipeline — protect the Collector from OOM
  under telemetry bursts.
- **Tail-based sampling at the gateway** — keep 100% of error/slow traces, sample
  the boring successes; needs the gateway to buffer whole traces.
- **Persistent sending queue + retry** — so a backend blip doesn't drop data.
- **Scale the gateway horizontally**, front it with a load balancer.
- **Secure it** — auth on receivers, TLS everywhere (we use `insecure` locally),
  and redact PII in a processor before it ever reaches a backend.
- **Custom distribution** — ship only the components you use.

---

## 8. Alternatives / relatives

- **Vendor agents** (Datadog Agent, Grafana Alloy, Fluent Bit for logs) — many now
  speak OTLP too.
- **Direct SDK export** (no collector) — fine for tiny setups; loses central
  processing.
- **Grafana Alloy** — Grafana's OTel-Collector-compatible distribution.
The value of OTel is that these interoperate via OTLP + semantic conventions.

---

## 9. Interview questions you should be able to answer

- *What is OpenTelemetry vs the Collector?* → OTel = the standard (API/SDK/OTLP/
  conventions) for generating telemetry; the Collector = a process that receives,
  processes, and routes it to backends.
- *Why run a Collector instead of exporting from the app directly?* → Decoupling
  from backends, central batching/sampling/redaction/enrichment, protocol
  translation, buffering/retry, offloading the app.
- *What are receivers/processors/exporters/pipelines?* → In: receivers; transform:
  processors; out: exporters; wired per-signal into pipelines.
- *Agent vs gateway deployment?* → Agent = per-host/sidecar local hop + enrichment;
  gateway = shared scaled cluster for central sampling/egress; often both.
- *What is OTLP?* → The OpenTelemetry wire protocol (gRPC 4317 / HTTP 4318) that
  everything speaks.
- *Where would you do tail-based sampling and why?* → At the gateway Collector,
  because it must see whole traces before deciding to keep them.
- *Core vs contrib?* → Minimal official build vs community components; prod often
  uses a custom-built minimal distribution.

---

## 10. How Ledgerstream uses it

Every service calls `configure_tracing()` from `ledgerstream_shared` at startup,
exporting **OTLP** to the Collector (`OTEL_EXPORTER_OTLP_ENDPOINT`). The Collector
(contrib image, single gateway instance in Docker) runs **two pipelines**: traces
→ **Jaeger**, metrics → re-exposed on `:8889` for **Prometheus** to scrape. Its
`health_check` extension backs the compose healthcheck. Because services only know
"send OTLP here," swapping or adding a backend is a one-file change — the whole
point of routing through a Collector.

---

*Related: [Observability (the 3 pillars)](observability.md) · [Jaeger](jaeger.md)
(where our traces land) · [Prometheus](prometheus.md) (which pulls our metrics) ·
[`docs/docker-compose-explained.md`](../docker-compose-explained.md).*
