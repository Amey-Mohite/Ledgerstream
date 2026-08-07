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
>   [phase6](docs/phase6.md) · [phase7](docs/phase7.md)
> - **[System-design handbook](docs/concepts/)** — Kafka, outbox, idempotency,
>   double-entry ledger, CAP/PACELC, schema evolution, consumer scaling, auth/JWT,
>   rate limiting, caching, circuit breakers, cursor pagination, load testing, **LLM
>   gateway, RAG/tool-use/MCP, AI guardrails**, **containers, Kubernetes/Helm, CI/CD,
>   Terraform/IaC**… written to interview depth with diagrams, code, and plain-English
>   analogies.

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
| **P7** | **Deployment & CI** — GitHub Actions (test → build → GHCR), one **DRY Helm chart** for all services + workers, **Terraform** to install it | ✅ |

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

## Visualize it — demo dashboards

Three GUIs let you *see* the system for a demo (all optional). Start the two containerized
ones on a **`tools` profile** so they never run in normal dev:

```bash
docker compose --profile tools up -d      # or: make tools-up
```

| System | Tool | Where | What you see |
|---|---|---|---|
| **Kubernetes** | [Lens](https://k8slens.dev/) or `k9s` | desktop / terminal | pods, deployments, logs, the live cluster |
| **Kafka** | **Kafka UI** (in `tools` profile) | http://localhost:8085 | topics (`payments.events`, `ledger.events`), the **Avro messages**, consumer groups + **lag**, Schema Registry |
| **Redis** | **RedisInsight** (in `tools` profile) | http://localhost:5540 | the gateway's **balances cache** keys (with TTLs) + **rate-limit token buckets** |

- **Kafka UI** auto-connects to the local broker + Schema Registry — open it and watch a
  `PaymentCaptured` land in `payments.events`, get consumed, and a `LedgerOutcome` appear in
  `ledger.events`.
- **RedisInsight** needs one connection added in its UI: point it at your **Upstash** URL
  (host, port `6379`, password, **TLS on** for `rediss://`), or the local `redis` container
  (`--profile full`). Run [`scripts/smoke_gateway.sh`](scripts/smoke_gateway.sh), then refresh
  to see the `balances:*` cache keys appear and expire.

**Running on Kubernetes?** The compose Kafka UI above is for the compose stack. When Kafka runs
*in-cluster* (Helm `--set kafka.enabled=true`, or `kubectl apply -f deploy/k8s/kafka.yaml`), a
Kafka UI is deployed in-cluster too — with **Helm it's automatic** (rendered by `kafka.enabled`;
skip with `--set kafka.uiEnabled=false`); with **raw manifests** install it explicitly:

```bash
kubectl apply -f deploy/k8s/kafka-ui.yaml
```

Then view it (both methods):
```bash
kubectl -n ledgerstream port-forward svc/kafka-ui 8085:8080
```

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

**CI runs the same suites on every push/PR** —
[`.github/workflows/ci.yml`](.github/workflows/ci.yml): shared-lib tests, the Django
services against real Postgres **service containers**, the hermetic gateway/ai suites,
then it builds all four images (→ GHCR on `main`).

---

## Deployment (Phase 7)

Containers → Kubernetes (Helm) → Terraform → CI. Full walkthrough:
[`docs/phase7.md`](docs/phase7.md). Concept docs:
[containers](docs/concepts/containers-and-images.md) ·
[k8s & Helm](docs/concepts/kubernetes-and-helm.md) ·
[CI/CD](docs/concepts/ci-cd-pipelines.md) ·
[IaC/Terraform](docs/concepts/infrastructure-as-code.md).

- **One image, many roles** — each service's image runs as the web process *and*, where it
  has Kafka workers, as those workers (same image, different `command` per Deployment). Four
  images, seven Deployments.
- **One DRY Helm chart** ([`deploy/helm/ledgerstream`](deploy/helm/ledgerstream)) renders
  every service + worker from a `services:` map (`helm lint`/`template` clean → 13 objects).
  Backing stores (Neon/Upstash/Atlas/Kafka) are **external inputs** (URLs), not chart objects.
- **Terraform** ([`deploy/terraform`](deploy/terraform)) installs the chart into a namespace
  and injects secrets via `set_sensitive` (`terraform validate` passes).

### Full local deploy flow (Kubernetes + Helm)

No cloud account needed — this uses **Docker Desktop's built-in Kubernetes** (Settings ⚙ →
Kubernetes → Enable). `kubectl` ships with Docker Desktop; you only add **Helm**. All commands
are **PowerShell**. (For `kind`/minikube variants, the raw-manifest alternative, and full
troubleshooting, see [`docs/phase7.md`](docs/phase7.md) Part 6.)

```powershell
# 0. Install Helm (one-time; no package manager needed). Then open a NEW terminal.
$dir = "$env:USERPROFILE\tools\helm"; New-Item -ItemType Directory -Force -Path $dir | Out-Null
Invoke-WebRequest -Uri "https://get.helm.sh/helm-v3.16.4-windows-amd64.zip" -OutFile "$dir\helm.zip"
Expand-Archive -Path "$dir\helm.zip" -DestinationPath $dir -Force
Copy-Item "$dir\windows-amd64\helm.exe" "$dir\helm.exe" -Force
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";$dir", "User")
```
```powershell
# 1. Confirm the cluster + tools (nodes should be Ready).
kubectl config use-context docker-desktop; kubectl get nodes; helm version
```
```powershell
# 2. Build the four images. ${s} braces are REQUIRED — PowerShell parses a bare $s:dev as a scoped variable.
foreach ($s in "payment","ledger","gateway","ai") { docker build -f services/$s/Dockerfile -t "ledgerstream-${s}:dev" . }
```
```powershell
# 3. Put your real secrets in variables (copy the values from your .env).
$JWT="paste-JWT_SIGNING_KEY"; $DJ="any-random-string"; $PAY_DB="paste-PAYMENT_DATABASE_URL"; $LED_DB="paste-LEDGER_DATABASE_URL"; $REDIS="paste-REDIS_URL"
```
```powershell
# 4. Install the chart (creates the namespace + all 13 objects; one line).
helm upgrade --install ledgerstream deploy/helm/ledgerstream --namespace ledgerstream --create-namespace --set image.registry=docker.io/library --set image.repositoryPrefix=ledgerstream --set image.tag=dev --set-string secretEnv.JWT_SIGNING_KEY="$JWT" --set-string secretEnv.DJANGO_SECRET_KEY="$DJ" --set-string secretEnv.PAYMENT_DATABASE_URL="$PAY_DB" --set-string secretEnv.LEDGER_DATABASE_URL="$LED_DB" --set-string secretEnv.REDIS_URL="$REDIS"
```
```powershell
# 5. Watch the pods until the *-web ones are Running.
kubectl -n ledgerstream get pods
```
```powershell
# 6. Reach the gateway (leave running; open a NEW terminal for step 7).
kubectl -n ledgerstream port-forward svc/ledgerstream-gateway 8010:8010
```
```powershell
# 7. Test it (new terminal).
curl.exe http://localhost:8010/health/ready
```
```powershell
# 8. Uninstall when done.
helm -n ledgerstream uninstall ledgerstream
```

**Expected:** the `*-web` pods go `Running`; the Kafka **worker** pods (`*-outbox-relay`,
`*-consume-*`) `CrashLoopBackOff` — that's normal (Kafka isn't in the cluster). **To run the
workers too, add Kafka in-cluster** — the simplest fix — by adding `--set kafka.enabled=true`
to step 4 (a single-node KRaft broker + Schema Registry, demo-grade). Then the workers connect
on their own. (Raw-manifest equivalent: `kubectl apply -f deploy/k8s/kafka.yaml`.)

### Alternative: raw manifests instead of Helm (`kubectl`, no Helm)

The same objects are also written as static, per-service YAML in [`deploy/k8s/`](deploy/k8s/)
(one file per service). Use these **OR** the Helm chart above — not both (same object names).
After building the images (step 2 above):

```powershell
# If a Helm release is up, remove it first (name clash), then set your secrets.
helm -n ledgerstream uninstall ledgerstream
$JWT="paste-JWT_SIGNING_KEY"; $DJ="any-random-string"; $PAY_DB="paste-PAYMENT_DATABASE_URL"; $LED_DB="paste-LEDGER_DATABASE_URL"; $REDIS="paste-REDIS_URL"
```
```powershell
# Namespace + ConfigMap only (the file intentionally contains NO Secret).
kubectl apply -f deploy/k8s/00-config.yaml
```
```powershell
# Create the Secret from your variables (keeps secrets out of the file). Must run BEFORE the pods.
kubectl -n ledgerstream create secret generic ledgerstream-secrets --from-literal=JWT_SIGNING_KEY="$JWT" --from-literal=DJANGO_SECRET_KEY="$DJ" --from-literal=PAYMENT_DATABASE_URL="$PAY_DB" --from-literal=LEDGER_DATABASE_URL="$LED_DB" --from-literal=REDIS_URL="$REDIS" --dry-run=client -o yaml | kubectl apply -f -
```
```powershell
# The four services, then watch + reach the gateway.
kubectl apply -f deploy/k8s/payment.yaml -f deploy/k8s/ledger.yaml -f deploy/k8s/gateway.yaml -f deploy/k8s/ai.yaml
kubectl -n ledgerstream get pods
kubectl -n ledgerstream port-forward svc/ledgerstream-gateway 8010:8010
```
```powershell
# Remove everything.
kubectl delete -f deploy/k8s/ai.yaml -f deploy/k8s/gateway.yaml -f deploy/k8s/ledger.yaml -f deploy/k8s/payment.yaml -f deploy/k8s/00-config.yaml
```

**Helm vs raw:** Helm renders the templates + applies + tracks a *release* (`helm uninstall`
removes all 13 objects at once); raw `kubectl apply` applies the finished YAML yourself, cleaned
up with `kubectl delete`. Same result. Details: [`deploy/k8s/README.md`](deploy/k8s/README.md).

> Tiering: **CI is load-bearing**; the Helm/Terraform artifacts are validated and locally
> runnable (as above) but not applied to a live paid cluster.

---

## Repository layout

```
.
├── .github/workflows/ci.yml    # CI: test (shared/django+pg/hermetic) → build 4 images → GHCR
├── deploy/
│   ├── helm/ledgerstream/      # ONE chart → all services + workers (values.yaml `services:` map)
│   └── terraform/              # kubernetes + helm providers → install the chart
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
make tools-up    Start demo dashboards: Kafka UI (:8085) + RedisInsight (:5540)
make down        Stop (keep data)      make clean   Stop + delete volumes
make health      Show container health make logs    Tail logs
make shared-test Run shared-library unit tests
```
