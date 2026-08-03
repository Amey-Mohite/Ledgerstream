"""Payment lifecycle operations — the business logic, kept out of the views.

Two operations:
- authorize_payment: create a payment (mock provider always approves in Phase 1),
  idempotent under a client-supplied key.
- capture_payment: AUTHORIZED -> CAPTURED, writing the PaymentCaptured event to
  the outbox IN THE SAME TRANSACTION (the outbox pattern), idempotent under retry.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction

from core.exceptions import InvalidStateTransition
from outbox.models import OutboxEvent

from .models import Payment

logger = logging.getLogger(__name__)


def authorize_payment(
    *,
    tenant_id,
    amount_minor: int,
    currency: str,
    reference: str,
    idempotency_key: str | None,
) -> tuple[Payment, bool]:
    """Return (payment, created). Safe to retry with the same idempotency_key.

    Idempotency has two layers:
    1. Fast path — look up an existing payment for (tenant, key) and return it.
    2. Race-safe path — if two identical requests slip past the check at once, the
       UNIQUE (tenant, key) constraint makes exactly one INSERT win; the loser
       catches IntegrityError and returns the winner. The DB is the referee.
    """
    if idempotency_key:
        existing = Payment.objects.for_tenant(tenant_id).filter(
            idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing, False

    # (A real provider authorization call would go here; mocked as success.)
    try:
        with transaction.atomic():
            payment = Payment.objects.create(
                tenant_id=tenant_id,
                amount_minor=amount_minor,
                currency=currency,
                reference=reference,
                idempotency_key=idempotency_key,
                status=Payment.Status.AUTHORIZED,
            )
        logger.info(
            "payment authorized",
            extra={"payment_id": str(payment.id), "tenant_id": str(tenant_id)},
        )
        return payment, True
    except IntegrityError:
        # Lost the race — another identical request created it first.
        payment = Payment.objects.for_tenant(tenant_id).get(
            idempotency_key=idempotency_key
        )
        return payment, False


@transaction.atomic
def capture_payment(*, tenant_id, payment_id, correlation_id: str = "") -> tuple[Payment, bool]:
    """AUTHORIZED -> CAPTURED, emitting PaymentCaptured to the outbox atomically.

    Returns (payment, event_emitted). Re-capturing an already-CAPTURED payment is
    a no-op that returns event_emitted=False (idempotent). Strong consistency: we
    SELECT ... FOR UPDATE so two concurrent captures can't both emit an event.
    """
    payment = (
        Payment.objects.select_for_update()
        .for_tenant(tenant_id)
        .get(id=payment_id)
    )

    if payment.status == Payment.Status.CAPTURED:
        return payment, False  # already done — idempotent

    if payment.status != Payment.Status.AUTHORIZED:
        raise InvalidStateTransition(
            f"Cannot capture a payment in state {payment.status}."
        )

    payment.status = Payment.Status.CAPTURED
    payment.save(update_fields=["status", "updated_at"])

    # SAME transaction as the status change → the dual-write problem is gone.
    OutboxEvent.objects.create(
        aggregate_type="Payment",
        aggregate_id=payment.id,
        event_type="PaymentCaptured",
        event_version=1,
        topic="payments.events",
        # Phase 2 will key by hash(tenant, account); a stable per-payment key now.
        partition_key=f"{tenant_id}:{payment.id}",
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "payment_id": str(payment.id),
            "tenant_id": str(tenant_id),
            "amount_minor": payment.amount_minor,
            "currency": payment.currency,
            "reference": payment.reference,
            "status": payment.status,
        },
    )
    logger.info(
        "payment captured; outbox event written",
        extra={"payment_id": str(payment.id), "tenant_id": str(tenant_id)},
    )
    return payment, True


@transaction.atomic
def compensate_payment(*, payment_id, reason: str = "") -> bool:
    """Saga compensation: the ledger REJECTED this payment → void it.

    Idempotent by state — a redelivered rejection finds it already VOIDED and
    no-ops. Returns True if it voided now, False if it was already voided/unknown.
    """
    payment = Payment.objects.select_for_update().filter(id=payment_id).first()
    if payment is None or payment.status == Payment.Status.VOIDED:
        return False
    payment.status = Payment.Status.VOIDED
    payment.save(update_fields=["status", "updated_at"])
    logger.info(
        "payment compensated (ledger rejected)",
        extra={"payment_id": str(payment_id), "reason": reason},
    )
    return True


def capture_payments_batch(*, tenant_id, payment_ids, correlation_id: str = "") -> list[dict]:
    """Capture many payments with PARTIAL-SUCCESS semantics.

    Each id is captured in its OWN transaction (reusing `capture_payment`), so one
    bad id never rolls back the others. Returns a per-item result list; the caller
    surfaces it as HTTP 207 Multi-Status. Each successful capture writes its own
    outbox row → its own Kafka event → its own ledger entry (per-account ordering
    preserved). Naturally idempotent: re-running yields `already_captured`.
    """
    results: list[dict] = []
    for pid in payment_ids:
        try:
            payment, emitted = capture_payment(
                tenant_id=tenant_id, payment_id=pid, correlation_id=correlation_id
            )
            results.append(
                {
                    "payment_id": str(pid),
                    "status": payment.status,
                    "outcome": "captured" if emitted else "already_captured",
                }
            )
        except (Payment.DoesNotExist, DjangoValidationError, ValueError):
            # Unknown id, another tenant's id, or a malformed UUID — don't leak,
            # don't crash the batch.
            results.append({"payment_id": str(pid), "outcome": "not_found"})
        except InvalidStateTransition as exc:
            results.append(
                {"payment_id": str(pid), "outcome": "invalid_state", "detail": str(exc)}
            )
    return results
