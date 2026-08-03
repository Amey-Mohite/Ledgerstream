"""Saga compensation: a rejected payment is voided, idempotently."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_compensate_voids_captured_payment_idempotently(make_tenant):
    from payments.models import Payment
    from payments.services import compensate_payment

    _tenant, client = make_tenant("Acme")
    pid = client.post("/api/payments", {"amount_minor": 500, "currency": "USD"}, format="json").data["id"]
    client.post(f"/api/payments/{pid}/capture")
    assert Payment.objects.get(id=pid).status == Payment.Status.CAPTURED

    assert compensate_payment(payment_id=pid, reason="ledger rejected") is True
    assert Payment.objects.get(id=pid).status == Payment.Status.VOIDED

    # redelivery → already voided → no-op
    assert compensate_payment(payment_id=pid) is False
    assert Payment.objects.get(id=pid).status == Payment.Status.VOIDED
