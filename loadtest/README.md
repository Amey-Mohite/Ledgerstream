# Load testing (Locust)

Drives the platform **through the gateway** to measure throughput + latency and to
watch the resilience patterns behave under load (rate-limit `429`s, cache hits,
consumer lag).

> **Run against full-local infra, not cloud free tiers.** Hammering Neon/Upstash/Atlas
> free tiers will hit *their* throttles and skew (or bill) your numbers — you'd be
> measuring the free tier, not the architecture. See `DESIGN.md` §Phase 5.

## 1. Install the tool

```bash
pip install -r loadtest/requirements.txt
```

## 2. Seed users + data

```bash
cd services/payment
python manage.py seed --tenants 5 --payments 100 --capture
```

This creates `load0`…`load4` (password `loadtestpw123`) and 100 captured payments per
tenant, so the ledger has balances/history to read.

## 3. Run the load test

```bash
USERS=5 locust -f loadtest/locustfile.py -H http://localhost:8010
```

Open **http://localhost:8089**, set the number of users + spawn rate, and start.
Headless example (100 users, 60s, CSV output):

```bash
USERS=5 locust -f loadtest/locustfile.py -H http://localhost:8010 \
  --headless -u 100 -r 20 -t 60s --csv results
```

## What to watch

| Signal | Where | Means |
|---|---|---|
| **RPS** | Locust "Total Requests/s" | throughput the stack sustains |
| **p95 / p99 latency** | Locust "Percentiles" | tail latency (what slow users feel) — see it climb past the "knee" |
| **429 rate** | per-endpoint failures (marked success here) | the token bucket shedding load — expected |
| **cache hit ratio** | gateway `X-Cache` / Redis | `GET /balances` mostly HIT under load |
| **consumer lag** | ledger consumer logs / Kafka | how far the ledger trails the write load |

Because the gateway rate-limits **per tenant**, one tenant caps at the bucket rate.
To measure *raw* throughput, seed more tenants (raise `USERS`) or bump
`RATE_LIMIT_CAPACITY` on the gateway. Full theory:
[docs/concepts/load-testing-and-performance.md](../docs/concepts/load-testing-and-performance.md).
