"""Seed tenants + users + payments — data for demos and load tests.

    # from services/payment
    python manage.py seed --tenants 3 --payments 100 --capture

Creates N tenants (each with one OWNER user), then authorizes `--payments` payments
per tenant, optionally capturing them (which writes the outbox events → relay →
Kafka → ledger). **Idempotent**: each payment uses a stable Idempotency-Key
(`seed-<tenant>-<i>`), so re-running tops up rather than duplicating, and users are
get_or_created. Prints the login creds so the Locust load test can use them.

Spreading load across MANY tenants matters for load testing: the gateway rate-limits
per tenant, so one tenant caps at the bucket rate — seed several and have Locust pick
among them (see loadtest/locustfile.py).
"""

from __future__ import annotations

import random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from payments.services import authorize_payment, capture_payment
from tenants.models import Membership, Tenant


class Command(BaseCommand):
    help = "Seed tenants + users + payments (optionally captured)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenants", type=int, default=1, help="number of tenants")
        parser.add_argument("--payments", type=int, default=50, help="payments per tenant")
        parser.add_argument("--user-prefix", default="load", help="usernames become <prefix><i>")
        parser.add_argument("--password", default="loadtestpw123")
        parser.add_argument("--currency", default="USD")
        parser.add_argument("--capture", action="store_true", help="also capture (writes outbox -> ledger)")

    def handle(self, *args, **opts) -> None:
        User = get_user_model()
        prefix, password = opts["user_prefix"], opts["password"]

        for t in range(opts["tenants"]):
            username = f"{prefix}{t}"
            # Tenant + user + membership (idempotent on the username).
            with transaction.atomic():
                user, created = User.objects.get_or_create(username=username)
                if created:
                    user.set_password(password)
                    user.save(update_fields=["password"])
                membership = getattr(user, "membership", None)
                if membership is None:
                    tenant = Tenant.objects.create(name=f"LoadTenant {t}")
                    Membership.objects.create(user=user, tenant=tenant, role=Membership.Role.OWNER)
                else:
                    tenant = membership.tenant

            captured = 0
            for i in range(opts["payments"]):
                payment, _ = authorize_payment(
                    tenant_id=tenant.id,
                    amount_minor=random.randint(100, 500_000),
                    currency=opts["currency"],
                    reference="seed",
                    idempotency_key=f"seed-{tenant.id}-{i}",   # stable → re-run safe
                )
                if opts["capture"]:
                    _, emitted = capture_payment(tenant_id=tenant.id, payment_id=payment.id)
                    captured += int(emitted)

            self.stdout.write(self.style.SUCCESS(
                f"tenant '{tenant.name}' [{tenant.id}] user={username} "
                f"payments={opts['payments']} captured={captured}"
            ))

        self.stdout.write("")
        self.stdout.write("Load test through the gateway with these creds:")
        self.stdout.write(
            f"  USERS={opts['tenants']} PASSWORD={password} "
            f"locust -f loadtest/locustfile.py -H http://localhost:8010"
        )
