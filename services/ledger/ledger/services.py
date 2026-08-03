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


# ponytail: demo business rule — the ledger only settles these currencies. Gives
# the saga a REJECTED path to exercise. Real rule would be account-status/limits.
SUPPORTED_CURRENCIES = {"USD", "EUR"}


@transaction.atomic
def post_payment_captured(event: dict) -> tuple[str, str]:
    """Handle a PaymentCaptured. Returns (status, reason):

      POSTED   -> balanced double-entry written (idempotent on event_id).
      REJECTED -> a ledger business rule failed; NO journal written.

    Deterministic: same event -> same outcome, so Kafka redeliveries are safe to
    replay (the caller re-emits the same LedgerOutcome; downstream dedupes).
    """
    currency = event["currency"]
    if currency not in SUPPORTED_CURRENCIES:
        return "REJECTED", f"unsupported settlement currency: {currency}"

    event_id = event["event_id"]
    # Fast-path idempotency: already posted -> still POSTED, don't double-write.
    if JournalEntry.objects.filter(event_id=event_id).exists():
        return "POSTED", ""

    tenant_id = event["tenant_id"]
    amount = event["amount_minor"]

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
        return "POSTED", ""

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
    return "POSTED", ""
