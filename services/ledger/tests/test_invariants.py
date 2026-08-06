"""Proof tests — the ledger's invariants hold over MANY random postings.

A double-entry ledger has guarantees that must never break, no matter the data. We
assert them over 50 randomized payments (a lightweight property test — random inputs,
invariant outputs — with no extra dependency). If the posting logic ever drifts, one
of these fails.
"""

from __future__ import annotations

import random

import pytest

pytestmark = pytest.mark.django_db


def test_double_entry_invariants_hold_over_random_payments(make_event):
    from ledger.models import JournalEntry, LedgerLine
    from ledger.services import post_payment_captured

    posted_total = 0
    for _ in range(50):
        amount = random.randint(1, 1_000_000)
        status, _reason = post_payment_captured(make_event(amount_minor=amount, currency="USD"))
        assert status == "POSTED"
        posted_total += amount

    # Invariant 1 — every journal entry is internally BALANCED (debits == credits).
    for entry in JournalEntry.objects.all():
        lines = list(entry.lines.all())
        debits = sum(l.amount_minor for l in lines if l.direction == LedgerLine.Direction.DEBIT)
        credits = sum(l.amount_minor for l in lines if l.direction == LedgerLine.Direction.CREDIT)
        assert debits == credits, f"entry {entry.event_id} is unbalanced"

    # Invariant 2 — the TRIAL BALANCE. Across ALL lines, total debits == total credits
    # == the money posted: every unit exists as both a debit and a credit, so the whole
    # book nets to zero. This is the fundamental accounting identity; if it ever fails,
    # money was created or destroyed.
    lines = list(LedgerLine.objects.all())
    debits = sum(l.amount_minor for l in lines if l.direction == LedgerLine.Direction.DEBIT)
    credits = sum(l.amount_minor for l in lines if l.direction == LedgerLine.Direction.CREDIT)
    assert debits == credits == posted_total


def test_replaying_events_never_double_posts(make_event):
    """Idempotency proof: re-delivering the same events leaves balances unchanged."""
    from ledger.models import LedgerLine
    from ledger.services import post_payment_captured

    events = [make_event(amount_minor=1000, currency="USD") for _ in range(10)]
    for e in events:
        post_payment_captured(e)
    after_first = LedgerLine.objects.count()

    for e in events:                    # replay every event (at-least-once delivery)
        post_payment_captured(e)
    assert LedgerLine.objects.count() == after_first   # no duplicates
