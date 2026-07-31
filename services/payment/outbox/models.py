"""The transactional outbox.

The dual-write problem: a service that must (1) change its database AND (2) publish
an event cannot do both atomically across two systems — a crash between them
leaves the DB and Kafka out of sync (money captured but no event, or vice versa).

The outbox pattern solves it: within the SAME database transaction as the business
change, we insert a row into this table. Because it's one local transaction, it's
all-or-nothing. A separate **relay worker** (Phase 2) then reads PENDING rows and
publishes them to Kafka, marking them PUBLISHED. Kafka delivery is at-least-once,
so consumers must be idempotent — but no event is ever lost.

This table is the "one atomic write" half of the pattern; the relay is the other.
"""

from __future__ import annotations

from django.db import models

from core.models import TimestampedModel, UUIDModel


class OutboxEvent(UUIDModel, TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"        # written, not yet published
        PUBLISHED = "PUBLISHED", "Published"  # relay pushed it to Kafka (Phase 2)
        FAILED = "FAILED", "Failed"           # relay gave up (→ DLQ, Phase 3)

    # What happened (the event identity + envelope).
    aggregate_type = models.CharField(max_length=64)   # e.g. "Payment"
    aggregate_id = models.UUIDField()                  # the payment's id
    event_type = models.CharField(max_length=64)       # e.g. "PaymentCaptured"
    event_version = models.PositiveIntegerField(default=1)

    # Where it's going (used by the relay in Phase 2).
    topic = models.CharField(max_length=128)           # e.g. "payments.events"
    # Kafka partition key — Phase 2 sets this to hash(tenant, account) for
    # per-account ordering. Stored now so the relay stays dumb.
    partition_key = models.CharField(max_length=256)

    tenant_id = models.UUIDField()                     # for scoping/auditing
    correlation_id = models.CharField(max_length=64, blank=True, default="")

    # The event body (JSON now; serialized to Avro at publish time in Phase 2).
    payload = models.JSONField()

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "outbox_event"
        indexes = [
            # The relay polls "oldest PENDING first" — this index makes that scan
            # cheap and keeps published rows out of the way.
            models.Index(
                fields=["status", "created_at"], name="outbox_pending_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type}({self.aggregate_id}) [{self.status}]"
