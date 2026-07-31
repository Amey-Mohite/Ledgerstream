"""The Payment aggregate.

Design choices worth defending:
- **Money is stored as an integer of minor units** (`amount_minor`, e.g. cents),
  never a float. Floats can't represent 0.10 exactly; for money that's a bug. The
  currency's exponent (2 for USD) tells you where the decimal point goes.
- **Idempotency** is enforced at the data layer by a UNIQUE (tenant, key)
  constraint, so a client retry with the same `Idempotency-Key` can never create
  two payments — even under a race — because the database rejects the duplicate.
- **Tenant scoping** goes through a manager (`for_tenant`) so a query is never
  accidentally written without a tenant filter.
"""

from __future__ import annotations

from django.db import models

from core.models import TimestampedModel, UUIDModel
from tenants.models import Tenant


class PaymentQuerySet(models.QuerySet):
    def for_tenant(self, tenant_id) -> "PaymentQuerySet":
        """Scope every read to one tenant. THE tenant-isolation choke point."""
        return self.filter(tenant_id=tenant_id)


class Payment(UUIDModel, TimestampedModel):
    class Status(models.TextChoices):
        AUTHORIZED = "AUTHORIZED", "Authorized"  # funds reserved, not yet taken
        CAPTURED = "CAPTURED", "Captured"        # funds taken → ledger event emitted
        VOIDED = "VOIDED", "Voided"              # authorization released
        FAILED = "FAILED", "Failed"

    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, related_name="payments"
    )
    # Client-supplied retry token. Null = caller didn't send one (still valid,
    # just not idempotent). Unique per tenant when present.
    idempotency_key = models.CharField(max_length=200, null=True, blank=True)

    amount_minor = models.BigIntegerField()          # e.g. 500 = $5.00
    currency = models.CharField(max_length=3)        # ISO 4217, e.g. "USD"
    reference = models.CharField(max_length=200, blank=True, default="")

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.AUTHORIZED
    )

    objects = PaymentQuerySet.as_manager()

    class Meta:
        db_table = "payment"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"],
                name="uniq_tenant_idempotency_key",
                # Only enforce uniqueness when a key was actually provided.
                condition=models.Q(idempotency_key__isnull=False),
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "created_at"], name="payment_tenant_idx"),
        ]

    def __str__(self) -> str:
        return f"Payment {self.id} {self.amount_minor} {self.currency} [{self.status}]"
