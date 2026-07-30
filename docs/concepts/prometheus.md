# Prometheus — a deep dive (metrics & monitoring)

> **In one sentence:** Prometheus is an open-source monitoring system that
> **pulls** numeric metrics from your services on a schedule, stores them in its
> own time-series database, and lets you query, graph, and alert on them with its
> query language **PromQL**.

> 🧊 **In plain terms:** Prometheus is a diligent nurse doing rounds. Every 15
> seconds it walks up to each patient (service), reads the vital-signs chart taped
> to the door (`/metrics`), and writes the numbers in its logbook with a timestamp.
> Over time the logbook lets it chart trends ("this patient's heart rate has been
> climbing for an hour") and ring an alarm when something crosses a line. Crucially,
> **the nurse comes to read the chart** — patients don't chase the nurse.

> 🍳 **Where it fits (the kitchen analogy):** Prometheus is the **wall of dials &
> gauges** — it reads the kitchen's numbers every few seconds and sets off an
> **alarm** when one crosses a line. It tells you *something's* wrong;
> [Jaeger](jaeger.md) tells you *where*; the
> [OTel Collector](opentelemetry-collector.md) is the intercom that carried the
> numbers here. Full three-tool picture: [Observability §0](observability.md).

> This is the **Prometheus component** deep-dive. For *what a metric/counter/
> histogram is* and *percentiles vs averages*, read [Observability](observability.md).

---

## 1. The pull model (Prometheus's defining choice)

Most systems *push* data out. Prometheus **pulls**: it periodically HTTP-GETs a
`/metrics` endpoint on each target and records what it finds.

```
   Prometheus                          Your service
   ----------                          ------------
   every 15s:  GET /metrics  ------->  responds with current metric values:
                             <-------  ledgerstream_requests_total{...} 4021
                                       ledgerstream_request_duration_seconds_bucket{le="0.1"} 3980
   store samples in local TSDB with a timestamp
```

**Why pull, not push (classic interview question):**
- **Target health for free** — if a scrape fails, Prometheus *knows that target is
  down* (the `up` metric goes 0). With push, silence is ambiguous (dead? or just
  quiet?).
- **Prometheus controls the load** — it decides scrape frequency; a burst of
  targets can't flood it.
- **Simpler targets** — a service just exposes a page; it doesn't need to know
  Prometheus's address or manage a connection.
- **Easy testing** — you can `curl /metrics` yourself to see exactly what
  Prometheus sees.

**The exception — Pushgateway:** short-lived **batch jobs** finish before any
scrape can reach them, so they *push* their final metrics to a **Pushgateway**,
which Prometheus then scrapes. Use it *only* for that case; it's an anti-pattern
for long-running services.

---

## 2. The data model: metric name + labels → time series

This is the heart of Prometheus. Every unique combination of **metric name +
labels** is a separate **time series**, and each series is a stream of
`(timestamp, float64)` **samples**.

```
   ledgerstream_requests_total{service="payment", route="/pay", status="200"}  -> series A
   ledgerstream_requests_total{service="payment", route="/pay", status="500"}  -> series B
   ledgerstream_requests_total{service="ledger",  route="/bal", status="200"}  -> series C
   ^-------------- metric name --------------^ ^------------- labels -------------^

   Each series over time:
     A: (10:00:00, 4000) (10:00:15, 4021) (10:00:30, 4050) ...
```

- **Metric name** — *what* is measured (`ledgerstream_requests_total`).
- **Labels** — key/value dimensions (`service`, `route`, `status`) that let you
  slice the data.
- **Sample** — one `(timestamp, value)` reading.

### ⚠️ Cardinality — the #1 Prometheus gotcha
**Every distinct label-value combination creates a new time series**, and
Prometheus holds series in memory. Put a **high-cardinality** label — like
`user_id`, `email`, or a raw `request_id` — on a metric and you get *millions* of
series → memory blowup → Prometheus falls over. **Rule: labels must be
low-cardinality** (bounded sets: status codes, routes, service names). IDs belong
in **traces/logs**, never in metric labels. This is the mistake that takes down
real Prometheus deployments.

---

## 3. The four metric types

- **Counter** — monotonically increasing (total requests, total errors). You never
  read it raw; you take its **`rate()`** (per-second increase). Resets to 0 on
  restart (PromQL handles that).
- **Gauge** — goes up and down (memory in use, queue depth, active connections).
  Read directly.
- **Histogram** — samples bucketed into cumulative ranges (`le="0.1"`, `le="0.5"`,
  …), enabling **percentile** estimates (p95/p99 latency) via `histogram_quantile()`.
  Computed **server-side** at query time.
- **Summary** — like a histogram but percentiles are computed **client-side** and
  can't be aggregated across instances. Prefer **histograms** for latency in a
  multi-instance system (that's what `ledgerstream_shared.metrics` uses).

---

## 4. Scraping & service discovery

This is the **actual file** `infra/prometheus/prometheus.yml`. It's tiny — a
global default plus a list of things to scrape:

```yaml
global:
  scrape_interval: 15s          # scrape every target every 15 seconds
  evaluation_interval: 15s      # evaluate alert/recording rules every 15s

scrape_configs:
  - job_name: prometheus        # job 1: Prometheus scrapes ITSELF
    static_configs:
      - targets: ["localhost:9090"]     # its own /metrics (self-monitoring)

  - job_name: otel-collector    # job 2: scrape the OTel Collector
    static_configs:
      - targets: ["otel-collector:8889"]  # where the Collector re-exposes app metrics
```

**Line by line:**
- **`global.scrape_interval: 15s`** — how often Prometheus walks up to each target
  and reads `/metrics`. Every series gets a fresh sample every 15s.
- **`global.evaluation_interval: 15s`** — how often it runs alerting/recording
  rules (we have none yet, but this is the cadence).
- **`scrape_configs`** — the list of **jobs** (groups of targets):
  - **`prometheus`** — it scrapes its *own* `/metrics` on `localhost:9090`, so it
    can monitor itself.
  - **`otel-collector`** — it scrapes `otel-collector:8889` (Docker DNS name +
    port), which is where the Collector **re-exposes** the app metrics it received
    over OTLP. So our services' metrics reach Prometheus via:
    `service → OTLP → Collector :8889 → Prometheus scrapes it`.

- **Static targets** (what we use locally) — a hardcoded list of hosts.
- **Service discovery** (production) — instead of a static list, Prometheus
  auto-discovers targets from **Kubernetes**, Consul, EC2, DNS, etc., so pods
  appearing/disappearing are picked up without editing config. This is how it
  scales to dynamic fleets.

> 🧊 **In plain terms:** the file is the nurse's rounds schedule — "every 15
> seconds, visit these two rooms and read their charts." One room is Prometheus's
> own; the other is the Collector, which holds all the app's charts.

The scraped page is in the **Prometheus exposition format** (plain text: one line
per series). Client libraries (`prometheus-client` in Python) generate it for you.

> In our stack, the **OTel Collector** re-exposes the app metrics it received (via
> OTLP) on `:8889`, and Prometheus scrapes *that*. So services push OTLP → Collector
> → Prometheus pulls. (Traces are pushed; metrics are pulled — note the asymmetry.)

---

## 5. PromQL — querying the data

PromQL operates on **vectors**:

- **Instant vector** — one value per series at a moment: `up`,
  `ledgerstream_requests_total`.
- **Range vector** — a window of samples per series: `ledgerstream_requests_total[5m]`.

Common patterns:
```promql
# per-second request rate over 5m, by service
sum by (service) (rate(ledgerstream_requests_total[5m]))

# error ratio
sum(rate(ledgerstream_requests_total{status=~"5.."}[5m]))
  / sum(rate(ledgerstream_requests_total[5m]))

# p99 latency from a histogram
histogram_quantile(0.99, sum by (le) (rate(ledgerstream_request_duration_seconds_bucket[5m])))
```

- **`rate()`** turns a counter into a per-second rate (and handles resets).
- **`sum by (label)`** aggregates across series (e.g. across instances).
- **`histogram_quantile()`** estimates percentiles from histogram buckets.
- `up` is a built-in series: `1` if the last scrape of a target succeeded, `0` if
  not — the simplest health signal.

---

## 6. Storage (the TSDB) and its limits

Prometheus has its **own** local time-series database:
- Recent samples buffer in memory + a **write-ahead log (WAL)**; older data is
  compacted into immutable on-disk **blocks** (2-hour windows).
- **Local retention is finite** (e.g. 15 days by default) — Prometheus is built for
  **recent, operational** monitoring, not infinite history.

**Scaling limits & the fix:** a single Prometheus is one machine — bounded by its
RAM/disk, no built-in clustering. For long-term storage, global view, or HA you use
**remote_write** to **Thanos**, **Cortex**, or **Grafana Mimir**, which add
long-term object-storage, deduplication, and horizontal scale on top of Prometheus.

---

## 7. Alerting

Prometheus evaluates **rules** on a schedule:
- **Recording rules** — precompute expensive queries into new series.
- **Alerting rules** — when a PromQL expression is true for a duration (e.g.
  `error_ratio > 0.05 for 10m`), fire an alert.
- Alerts go to a separate component, **Alertmanager**, which handles **grouping,
  deduplication, silencing, and routing** to Slack/PagerDuty/email. (Prometheus
  fires; Alertmanager decides who gets woken up.)

Visualization is usually **Grafana**, which queries Prometheus via PromQL — the UI
you'd build dashboards in (Prometheus's own UI is minimal, for ad-hoc queries).

---

## 8. Alternatives

- **VictoriaMetrics** — Prometheus-compatible, more efficient, easier long-term
  storage.
- **Thanos / Cortex / Mimir** — scale Prometheus, not replace it.
- **InfluxDB / Graphite** — other TSDBs (push-oriented).
- **Datadog / New Relic** — managed, metrics+traces+logs together, paid.
Most speak the Prometheus exposition format or `remote_write`, so they interoperate.

---

## 9. Interview questions you should be able to answer

- *How does Prometheus collect data?* → It **pulls** — scrapes `/metrics` HTTP
  endpoints on a schedule and stores timestamped samples.
- *Why pull over push?* → Free target-health detection (`up`), Prometheus controls
  load, simpler stateless targets, easy to test with curl. Pushgateway only for
  short-lived batch jobs.
- *Describe the data model.* → metric name + labels = a unique time series; each
  series is a stream of (timestamp, value) samples.
- *What is cardinality and why does it matter?* → Each label-value combo is a
  series held in memory; high-cardinality labels (user_id, email) explode series
  count and crash Prometheus — keep labels low-cardinality; IDs go in traces/logs.
- *Counter vs gauge vs histogram vs summary?* → up-only / up-down / bucketed for
  server-side percentiles / client-side percentiles. Use histograms for latency in
  multi-instance systems.
- *How do you get p99 latency?* → `histogram_quantile(0.99, rate(..._bucket[5m]))`
  over a histogram metric.
- *What's `rate()` for?* → Per-second rate of a counter, handling resets.
- *How does Prometheus scale beyond one node / keep long history?* → It doesn't
  natively; use remote_write to Thanos/Cortex/Mimir for HA + long-term storage.
- *Prometheus vs Alertmanager vs Grafana?* → Prometheus scrapes/stores/evaluates
  and fires alerts; Alertmanager routes/dedupes/silences them; Grafana visualizes.

---

## 10. How Ledgerstream uses it

Prometheus (Docker, UI at `http://localhost:9090`) scrapes itself and the **OTel
Collector** on `:8889`, where our services' metrics are re-exposed after arriving
via OTLP. The shared `ledgerstream_shared.metrics` defines low-cardinality
**counters** (requests, events consumed) and a **histogram** (request latency with
`service`/`route` labels — deliberately *not* per-user, to avoid a cardinality
blow-up). In later phases we'll watch **consumer lag**, **error rate**, and **p95/
p99 latency** here to verify the Ledger service keeps up with Payment. Production
would add Alertmanager + Grafana and remote_write to long-term storage.

---

*Related: [Observability](observability.md) · [OpenTelemetry Collector](opentelemetry-collector.md)
(exposes metrics Prometheus pulls) · [Jaeger](jaeger.md) (traces counterpart) ·
[`docs/docker-compose-explained.md`](../docker-compose-explained.md).*
