"""Proof that tenant A can never read or act on tenant B's data.

This is the multi-tenancy guarantee, verified end-to-end through the API (JWT →
tenant claim → data-layer scoping).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def _create_payment(client, amount=100, currency="USD"):
    return client.post(
        "/api/payments", {"amount_minor": amount, "currency": currency}, format="json"
    )


def test_tenant_cannot_read_another_tenants_payment(make_tenant):
    _tenant_a, client_a = make_tenant("Tenant A")
    _tenant_b, client_b = make_tenant("Tenant B")

    created = _create_payment(client_a)
    assert created.status_code == 201
    payment_id = created.data["id"]

    # B asks for A's payment by id → 404 (we don't even reveal it exists).
    resp = client_b.get(f"/api/payments/{payment_id}")
    assert resp.status_code == 404

    # B's own list is empty — it sees none of A's data.
    resp = client_b.get("/api/payments")
    assert resp.data == []

    # A can read its own payment.
    resp = client_a.get(f"/api/payments/{payment_id}")
    assert resp.status_code == 200
    assert resp.data["id"] == payment_id


def test_tenant_cannot_capture_another_tenants_payment(make_tenant):
    _tenant_a, client_a = make_tenant("Tenant A")
    _tenant_b, client_b = make_tenant("Tenant B")

    payment_id = _create_payment(client_a).data["id"]

    # B tries to capture A's payment → 404, and it must NOT be captured.
    resp = client_b.post(f"/api/payments/{payment_id}/capture")
    assert resp.status_code == 404

    from payments.models import Payment

    assert Payment.objects.get(id=payment_id).status == Payment.Status.AUTHORIZED


def test_unauthenticated_request_is_rejected(api_client):
    assert api_client.get("/api/payments").status_code == 401
