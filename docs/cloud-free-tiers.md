# Cloud free-tier setup guide

> **Goal:** get the backing data stores (Postgres ×2, Redis, MongoDB) running on
> free managed cloud tiers, so your **native services** connect to *real* remote
> infrastructure — TLS, connection strings, credentials, cold starts — the way
> production actually works. **Kafka + Schema Registry + observability stay in
> local Docker** (Kafka has no reliable free tier; see [§5](#5-kafka--why-it-stays-local-and-what-prod-looks-like)).

**What you'll end up with in `.env`:**

| Key | Provider | Free? |
|---|---|---|
| `PAYMENT_DATABASE_URL` | Neon | ✅ |
| `LEDGER_DATABASE_URL` | Neon (2nd database) | ✅ |
| `REDIS_URL` | Upstash | ✅ |
| `MONGO_URL` | MongoDB Atlas (M0) | ✅ (forever) |
| `KAFKA_BOOTSTRAP_SERVERS`, `SCHEMA_REGISTRY_URL` | local Docker | ✅ |

---

## 0. Read this first — the one security rule

- **Real connection strings go in `.env`** (which is gitignored). **Never in
  `.env.example`** (which is committed — placeholders only).
- Connection strings **contain passwords**. Treat the whole URL as a secret.
- This repo lives in **OneDrive**, which syncs to the cloud — so a secret pasted
  into a tracked file has already left your machine. If that happens,
  **rotate/reset the credential** in the provider console (takes seconds).

> 🧊 **In plain terms:** `.env.example` is the blank form you hand to everyone;
> `.env` is your filled-in copy with your real signature. Never sign the blank
> form that gets photocopied.

> **Free-tier limits change constantly.** This guide gives the *steps*, not exact
> quota numbers — **verify current limits at signup**. All four below are free to
> start with no card required (Atlas M0 asks for none; the others don't for the
> free tier).

---

## 1. Neon — PostgreSQL ×2

Neon is serverless Postgres. One **project** can hold multiple **databases**, so
we make **two databases in one project** — `payment` and `ledger`. They share the
Postgres instance but are separate databases (separate schemas, no cross-DB
queries) — the free-tier-friendly version of database-per-service.

> **Trade-off to note (and defend):** true database-per-service means separate
> *instances*. Two databases in one Neon project keeps the *logical* boundary
> (separate DBs, and we'll use separate roles) but not *physical* isolation. In
> production you'd use two separate Neon projects (or two RDS instances). We'll
> record this in DESIGN.md.

### Steps
1. Sign up at **neon.tech** (GitHub/Google login is fine).
2. **Create a project** — pick a region close to you (lower latency). It creates
   a default database (often `neondb`) and a role (e.g. `neondb_owner`).
3. **Create the two databases:** open the project → **Databases** → **New
   Database** → create `payment`, then again `ledger`. (You can reuse the default
   role as owner.)
4. **Get the connection string:** project **Dashboard** → **Connect** →
   copy the connection string. It looks like:
   ```
   postgresql://neondb_owner:<PASSWORD>@ep-xxxx.<region>.aws.neon.tech/neondb?sslmode=require
   ```
5. **Make the two URLs** — they're identical except the **database name** at the
   end. Swap `/neondb` for `/payment` and `/ledger`:
   ```
   PAYMENT_DATABASE_URL=postgresql://neondb_owner:<PASSWORD>@ep-xxxx.<region>.aws.neon.tech/payment?sslmode=require
   LEDGER_DATABASE_URL=postgresql://neondb_owner:<PASSWORD>@ep-xxxx.<region>.aws.neon.tech/ledger?sslmode=require
   ```
   Paste both into **`.env`**.

### Notes you should understand
- **`?sslmode=require`** — Neon only accepts TLS connections. Keep it. (This is
  real production behavior; your local Docker Postgres didn't need it.)
- **Pooled vs direct** — Neon offers a **pooled** connection string (host contains
  `-pooler`) and a direct one. Use **pooled** for the app (many short-lived
  connections); some migration tools prefer the **direct** one. We'll revisit in
  Phase 1 when Django connects; for now grab either.
- **Cold starts** — free-tier Neon *suspends* the database when idle; the first
  query after idle takes a second or two to wake. Normal; don't panic.

---

## 2. Upstash — Redis

Upstash is serverless Redis with a genuinely free tier (per-day command quota).

### Steps
1. Sign up at **upstash.com**.
2. **Create Database** → type **Redis** → pick a region near you → create.
3. On the database page, find the connection details. Copy the **`rediss://`**
   URL (note the **double `s`** = TLS), which embeds the password:
   ```
   rediss://default:<PASSWORD>@<host>.upstash.io:6379
   ```
4. Put it in **`.env`** as `REDIS_URL`.

### Notes
- **`rediss://` (TLS)** vs local `redis://` (plaintext) — again, real prod
  behavior. Our `REDIS_URL` handles both because the scheme carries the choice.
- Upstash counts **commands/day** on the free tier — fine for dev; just don't
  point a load test at it (that's why load tests run against local infra —
  see DESIGN.md).
- **Upstash discontinued their Kafka** product — Redis only. That's expected;
  Kafka stays in Docker.

---

## 3. MongoDB Atlas — the read model store

Atlas **M0** is free forever (512 MB).

### Steps
1. Sign up at **mongodb.com/cloud/atlas**.
2. **Create a cluster** → choose the **M0 free** tier → pick a cloud/region → create.
3. **Database Access** → **Add New Database User** → create a username + password
   (this is a *database* user, separate from your Atlas login). Save the password.
4. **Network Access** → **Add IP Address**. For local dev you can **Allow access
   from anywhere (`0.0.0.0/0`)**.
   > ⚠️ `0.0.0.0/0` means any IP can *attempt* to connect (they still need the
   > password). It's fine for a throwaway dev cluster; in production you'd
   > allowlist specific IPs / use VPC peering. Know this trade-off — it's an
   > interview-worthy security point.
5. **Connect** → **Drivers** → copy the connection string:
   ```
   mongodb+srv://<USER>:<PASSWORD>@<cluster>.mongodb.net/?retryWrites=true&w=majority
   ```
   Replace `<PASSWORD>` with the database user's password.
6. Put it in **`.env`** as `MONGO_URL`. Leave `MONGO_DB=ledger_read`.

### Notes
- **`mongodb+srv://`** — the `+srv` means "look up the cluster's nodes via DNS,"
  so one URL reaches a replicated cluster. Atlas M0 is already a small replica set
  — a taste of real HA.
- **`w=majority`** — write concern: a write isn't acknowledged until a **majority**
  of replicas have it (durability). Ties directly to the
  [consensus/quorum](concepts/consensus-and-coordination.md) concept.

---

## 4. Kafka + observability — local Docker

These stay local. Start just them (not the local data stores):

```bash
docker compose up -d --wait
```

With the profiles change (see `docker-compose.yml`), the **default** `up` starts
**Kafka + Schema Registry + OTel Collector + Jaeger + Prometheus** and *skips* the
local Postgres/Mongo/Redis (those are in the `full` profile for offline mode).

Your `.env` keeps the local values for these:
```
KAFKA_BOOTSTRAP_SERVERS=localhost:29092
SCHEMA_REGISTRY_URL=http://localhost:8081
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

---

## 5. Kafka — why it stays local, and what prod looks like

**Why local:** Kafka has **no reliable perpetual free tier** (Confluent Cloud is
credit-based and bills by the hour once credits lapse; Upstash dropped Kafka).
Since Kafka is the backbone we experiment with most (topics, partitions, replay,
DLQ), keeping it in local Docker means **zero billing risk and full control**.

**What you'd actually use in production** (the description you asked for):

| Option | What it is | When you'd pick it |
|---|---|---|
| **AWS MSK** | Managed Apache Kafka on AWS | You're on AWS and want real Kafka without running brokers |
| **Confluent Cloud** | Managed Kafka + Schema Registry by Kafka's creators | Best tooling/registry, multi-cloud; credit/usage-billed |
| **Redpanda Cloud** | Kafka-API-compatible, managed | Lower ops overhead, cost-sensitive |
| **Strimzi on Kubernetes** | Kafka operator you self-host on k8s | You run your own k8s and want control |

**What changes when you move Kafka to the cloud (and why our config already
anticipates it):**

- **Auth appears.** Local Kafka is `PLAINTEXT` (no auth). Managed Kafka requires
  **SASL** (username/password or API key) over **TLS**. Your client config gains:
  ```
  KAFKA_BOOTSTRAP_SERVERS=pkc-xxxx.region.aws.confluent.cloud:9092
  KAFKA_SECURITY_PROTOCOL=SASL_SSL
  KAFKA_SASL_MECHANISM=PLAIN
  KAFKA_SASL_USERNAME=<API_KEY>
  KAFKA_SASL_PASSWORD=<API_SECRET>
  ```
  Because we read all of this from **env vars**, switching from local to managed
  Kafka is a `.env` change, **not** a code change. That is exactly the
  12-factor/config-portability skill this project demonstrates.
- **Durability config becomes real.** Local is `RF=1, min.insync.replicas=1`
  (one broker). Production is **`RF=3`, `min.insync.replicas=2`, producer
  `acks=all`** — tolerate one broker failure with zero data loss (see
  [Kafka deep-dive §5](concepts/kafka.md#5-replication--how-kafka-doesnt-lose-your-data)).
- **Schema Registry** becomes the managed one (Confluent Cloud SR / AWS Glue
  Schema Registry) — same Avro schemas, a different `SCHEMA_REGISTRY_URL` + auth.
- **Managed brokers handle** partition rebalancing, upgrades, and scaling for you.

> **Interview line:** "Locally I run single-broker Kafka in Docker with RF=1 and
> plaintext; the client is fully env-configured, so production is a `.env` swap to
> MSK/Confluent with SASL_SSL and RF=3/minISR=2/acks=all — no code change."

---

## 6. Put it together & verify

1. **Fill `.env`** (copied from `.env.example`) with the four cloud URLs above.
2. **Start local infra:** `docker compose up -d --wait`.
3. **Quick connectivity checks** (once Phase 1 tooling exists we'll have proper
   health checks; for now a manual poke):

   ```bash
   # Postgres (needs psql, or use any GUI with the URL)
   psql "$PAYMENT_DATABASE_URL" -c "select 1;"

   # Redis (redis-cli with TLS)
   redis-cli -u "$REDIS_URL" ping        # -> PONG

   # Mongo (mongosh)
   mongosh "$MONGO_URL" --eval "db.runCommand({ ping: 1 })"
   ```
   (If you don't have these CLIs installed, don't worry — the Phase 1 services
   will verify their own connections at startup via readiness checks.)

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Postgres `SSL required` / connection refused | missing `?sslmode=require` | keep it on the Neon URL |
| Postgres first query hangs ~2s then works | Neon cold start (idle suspend) | expected; it woke up |
| Redis auth error | used `redis://` not `rediss://` | Upstash requires TLS — double-`s` |
| Mongo `authentication failed` | wrong db-user password, or `<PASSWORD>` left in URL | reset the **database** user password, re-paste |
| Mongo `connection timed out` | IP not allowlisted | Atlas → Network Access → add your IP / `0.0.0.0/0` |
| Anything: secret leaked into a tracked file | pasted a real URL into `.env.example` | move to `.env`, then **rotate** the credential |

---

*Related: [`docs/docker-compose-explained.md`](docker-compose-explained.md) (the
local Docker side + profiles) · [Kafka deep-dive](concepts/kafka.md) ·
[Schema Evolution](concepts/schema-evolution-and-contracts.md).*
