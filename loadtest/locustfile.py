"""Locust load test — drive the platform THROUGH THE GATEWAY (:8010).

Run (after seeding users):
    # 1. seed a few tenants + captured payments (from services/payment):
    python manage.py seed --tenants 5 --payments 100 --capture
    # 2. start the load test:
    USERS=5 locust -f loadtest/locustfile.py -H http://localhost:8010
    # 3. open http://localhost:8089 → set user count + spawn rate → watch RPS, p50/p95/p99.

Env vars:
    USERS       how many seeded tenants exist (each Locust user picks one at random)
    USER_PREFIX / PASSWORD   must match the `seed` run (defaults: load / loadtestpw123)

Read/write mix is realistic (reads ≫ writes). **429s are counted as EXPECTED, not
failures** — under load the token bucket *should* shed traffic; seeing 429s is the
rate limiter working, not a bug. Because the gateway rate-limits per tenant, spread
load across many seeded tenants (USERS) or raise RATE_LIMIT_CAPACITY on the gateway
to measure raw throughput rather than the (deliberate) per-tenant cap.
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, task

_USER_COUNT = int(os.getenv("USERS", "1"))
_PREFIX = os.getenv("USER_PREFIX", "load")
_PASSWORD = os.getenv("PASSWORD", "loadtestpw123")

_OK = (200, 429)   # 429 = rate-limited = the limiter doing its job, not an error


class LedgerUser(HttpUser):
    wait_time = between(0.1, 0.5)   # think-time between a user's requests

    def on_start(self) -> None:
        """Log in once per simulated user; reuse the token for the whole session."""
        username = f"{_PREFIX}{random.randint(0, _USER_COUNT - 1)}"   # spread across tenants
        resp = self.client.post(
            "/api/auth/token",
            json={"username": username, "password": _PASSWORD},
            name="POST /auth/token",
        )
        token = resp.json().get("access") if resp.status_code == 200 else None
        if token:
            self.client.headers.update({"Authorization": f"Bearer {token}"})

    @task(5)   # reads dominate
    def read_balances(self) -> None:
        with self.client.get("/api/balances", name="GET /balances", catch_response=True) as r:
            if r.status_code in _OK:
                r.success()

    @task(3)
    def read_transactions(self) -> None:
        with self.client.get("/api/transactions", name="GET /transactions", catch_response=True) as r:
            if r.status_code in _OK:
                r.success()

    @task(1)   # the write path: create → capture
    def create_and_capture(self) -> None:
        with self.client.post(
            "/api/payments",
            json={"amount_minor": random.randint(100, 50_000), "currency": "usd"},
            name="POST /payments",
            catch_response=True,
        ) as r:
            if r.status_code == 429:
                r.success()
                return
            if r.status_code != 201:
                r.failure(f"create returned {r.status_code}")
                return
            payment_id = r.json()["id"]
        self.client.post(f"/api/payments/{payment_id}/capture", name="POST /capture")
