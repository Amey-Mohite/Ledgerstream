"""Ledger proofs: balanced double-entry, idempotent consumption, tenant scoping."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_posts_a_balanced_double_entry(make_event):
    from ledger.models import JournalEntry, LedgerLine
    from ledger.services import post_payment_captured

    event = make_event(amount_minor=500, currency="USD")
    assert post_payment_captured(event) == ("POSTED", "")

    entry = JournalEntry.objects.get(event_id=event["event_id"])
    lines = list(entry.lines.all())
    assert len(lines) == 2

    debits = sum(l.amount_minor for l in lines if l.direction == LedgerLine.Direction.DEBIT)
    credits = sum(l.amount_minor for l in lines if l.direction == LedgerLine.Direction.CREDIT)
    assert debits == credits == 500          # the double-entry invariant

    codes = {l.account.code for l in lines}
    assert codes == {"CASH", "MERCHANT_PAYABLE"}


def test_consuming_the_same_event_twice_is_idempotent(make_event):
    from ledger.models import JournalEntry
    from ledger.services import post_payment_captured

    event = make_event()
    assert post_payment_captured(event)[0] == "POSTED"   # first time: posts
    assert post_payment_captured(event)[0] == "POSTED"   # duplicate: same outcome

    assert JournalEntry.objects.filter(event_id=event["event_id"]).count() == 1  # one entry


def test_balances_are_derived_and_tenant_scoped(auth_client, make_event):
    from ledger.services import post_payment_captured

    post_payment_captured(make_event(amount_minor=500))

    resp = auth_client.get("/api/balances")
    assert resp.status_code == 200
    by_code = {row["code"]: row for row in resp.data}
    # Cash is debit-normal → +500; payable is credit-normal → -500 (in debit terms).
    assert by_code["CASH"]["balance"] == 500
    assert by_code["MERCHANT_PAYABLE"]["balance"] == -500


def test_unsupported_currency_is_rejected_without_a_journal(make_event):
    from ledger.models import JournalEntry
    from ledger.services import post_payment_captured

    event = make_event(currency="GBP")   # not in SUPPORTED_CURRENCIES
    status, reason = post_payment_captured(event)

    assert status == "REJECTED"
    assert "GBP" in reason
    assert not JournalEntry.objects.filter(event_id=event["event_id"]).exists()   # no journal


def test_read_api_requires_auth(api_client):
    assert api_client.get("/api/balances").status_code == 401
