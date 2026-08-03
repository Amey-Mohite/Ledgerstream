# Phase 0 — Foundation, explained from scratch

> **Who this is for:** you, right now, with no prior knowledge of Kafka, Docker,
> or observability tooling. By the end you'll understand every file we created in
> Phase 0, what it is, why it exists, and how it works. Read it top to bottom
> once; come back to sections as reference.

> **Want the deeper theory?** This file explains *the files*. For the general
> **system-design principles** behind them (event-driven architecture, CAP/PACELC,
> consensus, schema evolution, observability, sagas…) — written to interview
> depth, not tied to this project — see the **[concepts handbook](concepts/)**.

There is **no application code** in Phase 0. We didn't build the payment or
ledger service yet. Phase 0 is the **stage** the actors will later walk onto:
the databases, the message bus, the monitoring, and a small shared toolkit.
Building the stage first means that when we write real features, we can *see*
them working immediately.

---

## Part 1 — The mental model (read this first)

### What is a "container"?
A container is a lightweight, isolated box that runs one program plus everything
that program needs (its OS libraries, config, etc.), so it runs the same on your
laptop as on a server. Think of it as a shipping container for software: sealed,
standardized, portable.

- **Docker** is the tool that runs containers.
- An **image** is the frozen template (e.g. "Postgres 16"); a **container** is a
  running copy of an image.

### What is `docker-compose`?
Our system has ~8 different programs (a database here, a message bus there, a
monitoring tool…). Starting each by hand would be painful. **Docker Compose**
lets us describe all of them in **one file** (`docker-compose.yml`) and start
them together with one command. It also wires them onto a shared private network
so they can talk to each other by name (e.g. a service can reach the database at
the hostname `postgres-payment`).

### Why so many pieces? (the 30-second tour)
Our future system is **event-driven and multi-service**. That means:

- Services don't call each other directly; they **send messages** through a
  message bus → that's **Kafka**.
- Each service keeps its own **database** → that's **Postgres** (×2 so far).
- We want to *see* what's happening across services → that's **observability**
  (OpenTelemetry + Jaeger + Prometheus).
- We'll cache and rate-limit later → **Redis**.
- We'll build a fast read-only view later → **MongoDB**.

Phase 0 stands all of these up, empty and healthy.

---

## Part 2 — The repository, file by file

```
Ledgerstream/
├── .gitignore                     # what Git should ignore
├── .env.example                   # environment-variable template
├── docker-compose.yml             # defines & wires all containers
├── Makefile                       # shortcut commands
├── README.md                      # quick-start for a newcomer
├── DESIGN.md                      # the "why" behind every decision
├── docs/
│   └── phase0.md                  # this file
├── infra/
│   ├── otel/otel-collector-config.yaml
│   └── prometheus/prometheus.yml
├── libs/
│   └── shared/                    # a small Python library shared by services
│       ├── pyproject.toml
│       ├── README.md
│       ├── ledgerstream_shared/
│       │   ├── __init__.py
│       │   ├── config.py
│       │   ├── correlation.py
│       │   ├── logging.py
│       │   ├── metrics.py
│       │   └── tracing.py
│       └── tests/
│           ├── test_logging.py
│           └── test_correlation.py
└── services/                      # empty for now; real services land in Phase 1
```

### Root-level files

#### `.gitignore`
- **What:** a list of file patterns Git should *not* track.
- **Why:** things like `__pycache__/` (Python's compiled cache), `.venv/`
  (virtual environments), and crucially **`.env`** (which will hold secrets)
  must never be committed. Committing secrets is one of the most common real
  security mistakes.
- **How:** Git reads this file and silently skips anything matching a pattern.

#### `.env.example`
- **What:** a **template** listing every environment variable the system needs,
  with safe dev placeholder values.
- **Why:** software shouldn't hardcode settings like database passwords or
  hostnames — those change between your laptop and production. Instead the
  program reads them from the **environment**. We commit the *template*
  (`.env.example`) so anyone knows what to set, but never the real filled-in
  `.env` (which is gitignored).
- **How:** you copy it to `.env`; Docker Compose reads `.env` and injects those
  values into the containers. `${PAYMENT_DB_USER:-payment}` in the compose file
  means "use the env var `PAYMENT_DB_USER`, or fall back to `payment`."
- **Key idea to remember:** *"no secrets in code."* In production these values
  come from a secrets manager (Vault, AWS SSM), not a file — the `.env` file is a
  local-dev convenience only.

#### `docker-compose.yml` (the heart of Phase 0)
> 📄 **Full line-by-line walkthrough:**
> [`docs/docker-compose-explained.md`](docker-compose-explained.md) — including
> the tricky Kafka listener config. The summary below is enough to follow along.

- **What:** the single file that declares all 8 containers, their settings,
  networks, storage, and health checks.
- **Why:** one command (`docker compose up`) then starts the whole system.
- **How — a few concepts you'll see in it:**
  - **`services:`** — each entry is one container (kafka, redis, …).
  - **`image:`** — which prebuilt template to run (e.g. `postgres:16`).
  - **`ports: "5433:5432"`** — "map port 5432 *inside* the container to 5433 on
    your laptop." So you connect to `localhost:5433`. We shift some ports (5433,
    5434, 29092) so they don't clash with anything already on your machine.
  - **`volumes:`** — persistent storage. Containers are disposable; a volume is a
    disk that survives restarts, so your database data isn't lost when you stop a
    container.
  - **`networks:`** — a private network so containers reach each other by name.
  - **`healthcheck:`** — a command Docker runs repeatedly to ask "are you ready?"
    (e.g. "can I ping this database?"). Until it passes, the container is
    "starting," not "healthy."
  - **`depends_on: condition: service_healthy`** — "don't start me until that
    other container is *healthy*." This is why Schema Registry waits for Kafka.
  - **`--wait`** (used by `make up`) — the command blocks until **everything** is
    healthy, so when it returns, the whole system is genuinely ready.

#### `Makefile`
- **What:** a collection of named shortcut commands.
- **Why:** instead of memorizing long Docker commands, you type `make up`,
  `make down`, `make health`, `make shared-test`. It's a convenience + a form of
  documentation (the targets show the common operations).
- **How:** each target (e.g. `up:`) lists the real command it runs underneath.
  (On Windows `make` isn't installed by default — the README shows the raw
  `docker compose` commands as a fallback.)

#### `README.md`
- **What:** the front door — how to run the project and where to click.
- **Why:** the first thing an interviewer (or future you) reads.

#### `DESIGN.md`
- **What:** the deep "why" document — requirements, capacity math, consistency
  choices, trade-offs.
- **Why:** code shows *what*; DESIGN.md shows *why*. This is what you study before
  an interview. `docs/phaseN.md` (this file) teaches the *how*; DESIGN.md argues
  the *decisions*.

---

## Part 3 — The `infra/` config files

These are configuration files handed to two of the containers.

#### `infra/prometheus/prometheus.yml`
- **What is Prometheus?** A monitoring system that **collects numeric metrics**
  over time — "how many requests per second," "how long did they take,"
  "how many events consumed." It stores them and lets you graph/alert on them.
- **What this file does:** tells Prometheus *where to look* for metrics. It
  "scrapes" (fetches) metrics every 15 seconds from itself and from the OTel
  Collector (which re-publishes our services' metrics).
- **How Prometheus works, in one line:** it **pulls** — it periodically calls a
  `/metrics` URL on each target and records the numbers. (Contrast with logs,
  which are pushed out as they happen.)

#### `infra/otel/otel-collector-config.yaml`
- **What is OpenTelemetry (OTel)?** An industry-standard way for programs to emit
  **traces** and **metrics**. A **trace** follows one request as it hops across
  services, so you can see the whole journey and where time was spent.
- **What is the Collector?** A middle-man container. Every service sends its
  traces/metrics to the Collector (one place), and the Collector forwards them to
  the right backends. This file wires that up:
  - **receivers** — how data comes *in* (OTLP, the standard protocol, on ports
    4317/4318).
  - **processors** — `batch` groups data for efficiency.
  - **exporters** — where data goes *out*: traces → **Jaeger**, metrics →
    **Prometheus**.
  - **pipelines** — connect receivers → processors → exporters.
- **Why a Collector instead of services talking to Jaeger/Prometheus directly?**
  Decoupling. Services only need to know "send to the Collector." If we later
  swap Jaeger for another tool, we change *one* config file, not every service.

---

## Part 4 — The eight containers, each explained

| Container | What it is | Why it's here |
|---|---|---|
| **kafka** | A **message bus / event log**. Services write ("produce") messages to named streams called **topics**; other services read ("consume") them. | It's the backbone. Services coordinate by exchanging events, not by calling each other directly — so they stay decoupled and independently deployable. |
| **schema-registry** | A service that stores the **shape (schema)** of each event and enforces rules when that shape changes. | Events are contracts between services. The registry guarantees a new version of an event won't silently break an old consumer. |
| **postgres-payment** | A **SQL relational database** for the Payment service. | Payments are money — they need ACID transactions (all-or-nothing writes). Each service gets its *own* database (no sharing) to stay independent. |
| **postgres-ledger** | A second SQL database for the Ledger service. | Same reason; separate so the two services never reach into each other's data. |
| **mongo** | A **NoSQL document database**. | Later we build a fast, denormalized read-only view of transactions here — a good fit for a rebuildable, read-optimized copy. |
| **redis** | An **in-memory key-value store** (very fast). | Later used for caching (avoid re-computing expensive reads) and rate-limiting (counting requests per API key). |
| **otel-collector** | The observability middle-man (see Part 3). | Central intake for traces + metrics from every service. |
| **jaeger** | A **trace-viewer UI** (http://localhost:16686). | Lets you *see* a single request's path across services and where it spent time. |
| **prometheus** | A **metrics database + UI** (http://localhost:9090). | Stores and graphs the numeric health of the system. |

### A closer look at the confusing ones

**Kafka (and "KRaft").** Kafka stores streams of events durably and hands them to
consumers in order. Historically Kafka needed a separate helper called ZooKeeper
to track cluster metadata (who's the leader, which topics exist). **KRaft** is
the newer mode where Kafka manages that itself using the **Raft** consensus
algorithm — so we don't run ZooKeeper. In our setup one container is *both* the
"broker" (stores messages) and the "controller" (tracks metadata). In production
you'd run several brokers for redundancy; one is fine to learn on.

> **You should be able to say:** "Kafka is a distributed, append-only log of
> events organized into topics; producers write, consumers read in order; KRaft
> replaces ZooKeeper for metadata using Raft consensus."

**Schema Registry + Avro.** **Avro** is a compact binary format for encoding an
event. Because it's binary, both sides must agree on the schema. The **Schema
Registry** is the shared place that agreement lives. We set it to **BACKWARD**
compatibility, meaning: *a new version of a schema must still be able to read data
written by the old version.* That's what lets you evolve events over time without
breaking running consumers.

---

## Part 5 — The shared library (`libs/shared/`)

Our services will need the same plumbing: structured logging, request tracing,
metrics, correlation IDs. Rather than copy-paste that into each service, we put
it in **one small installable Python package** — `ledgerstream-shared` — and each
service depends on it.

> **Important design rule:** this library imports **no web framework** (no
> Django, no FastAPI). That keeps it neutral so pulling it into one service never
> drags in another's stack. The framework-specific glue lives in each service.

> 🧊 **In plain terms:** it's a **shared toolbox**. Every workshop (service) needs
> the same basic tools (hammer, tape measure). Instead of buying a separate set per
> shop — which drift and confuse — you keep **one standardized toolbox** everyone
> uses.

> **Deeper dive:** for the full from-scratch build — `pyproject.toml` section by
> section, distribution vs import name, extras, editable vs frozen installs, and how
> to add a module — see [concepts/internal-shared-library.md](concepts/internal-shared-library.md).

### How this package was created (and the "clutter" around it)

The **source files** were hand-written in Phase 0: `pyproject.toml`, the modules
in `ledgerstream_shared/`, the `tests/`, and the README. The **other folders you
may see** — `.egg-info/`, `__pycache__/`, `.pytest_cache/` — were **not** written
by hand; they appear automatically when the package is *installed* (`pip install
-e`) and its *tests are run* (`pytest`). They're build/cache artifacts, are
**gitignored**, and regenerate if deleted. Ignore them.

### How the shared library gets deployed (where it "sits")

This is the part that confuses people: **the shared library is NOT deployed as its
own service.** It doesn't run on its own. It's a **dependency baked *into* each
service's Docker image at build time** — so a *copy* of it lives inside every
service.

```
   libs/shared  (source, in the repo)
        │  copied + installed at BUILD time into each image
        ├──────────────► payment image   (contains its own copy)
        ├──────────────► ledger image    (contains its own copy)
        ├──────────────► gateway image   (contains its own copy)
        └──────────────► ai image        (contains its own copy)
```

Each service's `Dockerfile` (built in Phase 1) copies the library in and installs
it:

```dockerfile
# (Payment service Dockerfile — Phase 1)
FROM python:3.12-slim
COPY libs/shared /app/libs/shared
RUN pip install /app/libs/shared          # ledgerstream_shared now lives INSIDE this image
COPY services/payment /app/services/payment
RUN pip install -r /app/services/payment/requirements.txt
CMD ["gunicorn", "config.wsgi"]
```

So after `docker build`, the Payment image contains **both** the Payment code
**and** a copy of `ledgerstream_shared`. There's **no separate "shared-lib
container."** Deploy 4 services as 4 pods → the library exists as 4 copies, one
baked into each.

> 🧊 **In plain terms:** the library is an **ingredient**, not a dish. You don't
> serve flour on its own plate — you **bake it into each cake**. Every service-cake
> contains the shared flour inside it.

**Two ways to distribute a shared library** (know both for interviews):

| Approach | How | Trade-off |
|---|---|---|
| **Monorepo + build-time install** (ours) | Lib in the same repo; each Dockerfile copies + installs it | Simple; all services always on the *same* version. Rebuild all to update. |
| **Published package** (large orgs) | Publish `ledgerstream-shared==1.2.0` to a private registry (private PyPI / CodeArtifact / Artifactory); each service pins a version | More decoupled; services upgrade independently. More setup. |

We use the **monorepo** approach — simplest for one developer and guarantees every
service runs the exact same plumbing version.

### `pyproject.toml`
- **What:** the package's definition file — its name, version, dependencies, and
  how to install it.
- **Why:** makes `ledgerstream-shared` a real, installable package (`pip install
  -e libs/shared`) instead of loose files.
- **How:** lists dependencies (OpenTelemetry, prometheus-client) that get pulled
  in automatically on install.

### `ledgerstream_shared/__init__.py`
- **What:** marks the folder as a Python package and defines what's public.
- **Why:** so `from ledgerstream_shared.logging import configure_logging` works.

### `config.py` — reading settings safely
- **What:** helpers to read environment variables with types and validation:
  `require_env` (crash at startup if a critical value is missing), `get_int`,
  `get_bool`.
- **Why:** a service should **fail fast** at boot if, say, its database URL is
  missing — much better than a confusing crash deep inside a request an hour
  later.

### `correlation.py` — following one request across services
- **What:** stores a **correlation ID** — a unique tag attached to one logical
  request — and provides get/set helpers.
- **Why:** when a request touches the gateway → payment → ledger, you want every
  log line and trace from all three to share one ID, so you can filter the logs
  for `correlation_id = abc123` and see the *whole* story.
- **How (the clever bit):** it uses Python's **`contextvars`**. A context
  variable is like a global variable, but automatically isolated per request —
  each web request (whether a Django thread or a FastAPI async task) sees its own
  value with no leakage between concurrent requests. That's why we don't use a
  plain global (would be shared by everyone) or a thread-local (breaks with async).

> **You should be able to say:** "A correlation ID ties all logs/traces of one
> request together; we store it in a contextvar so it's isolated per request
> across both threads and coroutines."

### `logging.py` — logs a machine can search
- **What:** configures logging so every log line is printed as a **JSON object**
  (`{"timestamp":..., "level":"INFO", "service":"payment", "correlation_id":...,
  "message":...}`) instead of a plain sentence.
- **Why:** in a multi-service system you ship all logs to one place and search
  them by field. JSON is directly queryable ("show me all ERROR logs for
  tenant X"); plain text forces fragile pattern-matching.
- **How:** a custom `JsonFormatter` builds that JSON, and it automatically pulls
  in the current correlation ID from `correlation.py` — so you never have to
  remember to add it.

### `metrics.py` — counting things for Prometheus
- **What:** defines the standard **metrics** our services will report: a counter
  of requests, a histogram of how long requests take, a counter of Kafka events
  consumed.
- **Why:** consistent metric names across every service (no copy-paste drift), so
  Prometheus dashboards work uniformly.
- **How:** uses the `prometheus-client` library. HTTP services will expose these
  at a `/metrics` URL; background workers (which have no web server) can start a
  tiny metrics server via `start_metrics_server`.

### `tracing.py` — turning on distributed tracing
- **What:** sets up **OpenTelemetry** so a service emits **spans** (timed segments
  of work) that form **traces**.
- **Why:** to answer "where did this slow request spend its time?" across
  services in the Jaeger UI.
- **How:** it configures a "tracer provider" that sends spans over OTLP to the
  Collector. If no Collector is configured (e.g. during a unit test), it safely
  prints spans to the console instead of crashing — that's the "degrade
  gracefully" bit.

### `tests/` — proof the library works
- **What:** automated tests (`test_logging.py`, `test_correlation.py`) that check
  the logging output is valid JSON, the correlation ID is injected, etc.
- **Why:** so we *know* the shared plumbing works before any service depends on
  it. We ran these: **10/10 pass.**
- **How:** run them with `make shared-test` (or `pytest libs/shared`).

---

## Part 6 — How it all fits together (the flow, once services exist)

Even though services aren't built yet, here's the picture Phase 0 sets up:

```
A request arrives → gets a correlation ID (correlation.py)
     │
     ├─ every log line is JSON tagged with that ID (logging.py)
     ├─ a trace/span is recorded and sent to the Collector (tracing.py)
     │        → Collector forwards the trace to Jaeger (you view it)
     ├─ metrics (count, latency) are recorded (metrics.py)
     │        → Collector exposes them, Prometheus scrapes them (you graph them)
     │
     └─ the service reads/writes its OWN Postgres, and (later) produces/consumes
        events on Kafka, whose shapes are governed by the Schema Registry.
```

Phase 0 built every box in that diagram *except* the services themselves — which
is exactly what Phase 1 starts.

---

## Part 7 — Mini-glossary

| Term | Plain meaning |
|---|---|
| **Container / image** | A sealed box that runs one program / its frozen template. |
| **Docker Compose** | Tool to run many containers together from one file. |
| **Volume** | Persistent disk for a container so data survives restarts. |
| **Kafka topic** | A named stream of events; producers write, consumers read. |
| **Broker / controller** | Kafka node that stores messages / tracks cluster metadata. |
| **KRaft** | Kafka's built-in metadata mode (replaces ZooKeeper). |
| **Schema** | The agreed shape/structure of an event. |
| **Avro** | Compact binary format for encoding events. |
| **Schema Registry** | Central store of schemas + evolution rules. |
| **SQL vs NoSQL** | Structured, transactional (Postgres) vs flexible documents (Mongo). |
| **Redis** | Fast in-memory store for caching / counters. |
| **Metric** | A number tracked over time (count, latency). |
| **Trace / span** | The path of one request across services / one timed step of it. |
| **Correlation ID** | A tag shared by all logs/traces of one request. |
| **Health check** | A command Docker runs to decide if a container is ready. |
| **Environment variable** | A setting passed in from outside the program. |

---

## Part 8 — What to try right now (hands-on)

1. **Start Docker Desktop**, then from the project folder:
   ```bash
   docker compose up -d --wait
   ```
   Wait for it to report everything healthy.
2. **Open the UIs:**
   - Jaeger: http://localhost:16686 (empty for now — no services emitting traces yet)
   - Prometheus: http://localhost:9090 (try the query box: type `up` and hit Execute — you'll see which targets are alive)
   - Schema Registry: http://localhost:8081/subjects (returns `[]` — no schemas yet)
3. **Run the shared-library tests:**
   ```bash
   pip install -e libs/shared[dev]
   pytest libs/shared -v
   ```
4. **Tear down when done:** `docker compose down` (keeps data) or `down -v`
   (deletes it).

Poke around, break things, restart. Nothing here is precious — that's the point
of Phase 0.

---

*Next: `docs/phase1.md` will explain the Payment service the same way — every
model, view, and the outbox pattern, from scratch.*
