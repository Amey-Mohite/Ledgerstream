"""Post ledger entries from consumed events.

`post_payment_captured` turns one PaymentCaptured event into a balanced
double-entry journal:  DEBIT Cash / CREDIT Merchant-Payable  (money received is
now owed to the merchant). It is **idempotent** — consuming the same event twice
posts exactly one entry — because `JournalEntry.event_id` is UNIQUE.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction

from .models import Account, JournalEntry, LedgerLine

logger = logging.getLogger(__name__)


def _get_account(tenant_id, code: str, account_type: str) -> Account:
    account, _created = Account.objects.get_or_create(
        tenant_id=tenant_id, code=code, defaults={"type": account_type}
    )
    return account


def _assert_balanced(entry: JournalEntry) -> None:
    debits = sum(l.amount_minor for l in entry.lines.all() if l.direction == LedgerLine.Direction.DEBIT)
    credits = sum(l.amount_minor for l in entry.lines.all() if l.direction == LedgerLine.Direction.CREDIT)
    if debits != credits:
        # A ledger that doesn't balance is a bug we must never persist.
        raise ValueError(f"Unbalanced entry {entry.id}: debits={debits} credits={credits}")


@transaction.atomic
def post_payment_captured(event: dict) -> bool:
    """Post the double-entry for a PaymentCaptured event.

    Returns True if a new entry was posted, False if this event was already
    processed (idempotent no-op).
    """
    event_id = event["event_id"]

    # Fast-path idempotency check.
    if JournalEntry.objects.filter(event_id=event_id).exists():
        logger.info("duplicate event ignored", extra={"event_id": str(event_id)})
        return False

    tenant_id = event["tenant_id"]
    amount = event["amount_minor"]
    currency = event["currency"]

    cash = _get_account(tenant_id, "CASH", Account.Type.ASSET)
    payable = _get_account(tenant_id, "MERCHANT_PAYABLE", Account.Type.LIABILITY)

    try:
        entry = JournalEntry.objects.create(
            tenant_id=tenant_id,
            event_id=event_id,                       # UNIQUE → race-safe idempotency
            description=f"PaymentCaptured {event['payment_id']}",
            occurred_at=event["occurred_at"],
            correlation_id=event.get("correlation_id", ""),
        )
    except IntegrityError:
        # Another consumer instance posted this event first.
        logger.info("event already posted (race)", extra={"event_id": str(event_id)})
        return False

    # The double entry: debits == credits.
    LedgerLine.objects.create(
        entry=entry, account=cash, direction=LedgerLine.Direction.DEBIT,
        amount_minor=amount, currency=currency,
    )
    LedgerLine.objects.create(
        entry=entry, account=payable, direction=LedgerLine.Direction.CREDIT,
        amount_minor=amount, currency=currency,
    )
    _assert_balanced(entry)

    logger.info(
        "posted double-entry",
        extra={"event_id": str(event_id), "tenant_id": str(tenant_id), "amount_minor": amount},
    )
    return True
