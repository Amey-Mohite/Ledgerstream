"""Proof that retries don't double-charge (idempotency) and capture is atomic."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

BODY = {"amount_minor": 500, "currency": "USD"}


def test_same_idempotency_key_creates_one_payment(make_tenant):
    tenant, client = make_tenant("Acme")
    headers = {"HTTP_IDEMPOTENCY_KEY": "retry-token-123"}

    first = client.post("/api/payments", BODY, format="json", **headers)
    second = client.post("/api/payments", BODY, format="json", **headers)

    # First creates (201); the retry replays the same payment (200), same id.
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.data["id"] == second.data["id"]

    from payments.models import Payment

    assert Payment.objects.for_tenant(tenant.id).count() == 1


def test_missing_idempotency_key_creates_distinct_payments(make_tenant):
    tenant, client = make_tenant("Acme")

    a = client.post("/api/payments", BODY, format="json")
    b = client.post("/api/payments", BODY, format="json")

    # No key supplied → two independent authorizations (both 201, different ids).
    assert a.status_code == b.status_code == 201
    assert a.data["id"] != b.data["id"]

    from payments.models import Payment

    assert Payment.objects.for_tenant(tenant.id).count() == 2


def test_same_key_different_tenants_are_independent(make_tenant):
    _tenant_a, client_a = make_tenant("A")
    _tenant_b, client_b = make_tenant("B")
    headers = {"HTTP_IDEMPOTENCY_KEY": "shared-key"}

    ra = client_a.post("/api/payments", BODY, format="json", **headers)
    rb = client_b.post("/api/payments", BODY, format="json", **headers)

    # The same key in two tenants must create two separate payments (uniqueness
    # is scoped per tenant, not global).
    assert ra.status_code == rb.status_code == 201
    assert ra.data["id"] != rb.data["id"]


def test_capture_transitions_and_writes_exactly_one_outbox_event(make_tenant):
    _tenant, client = make_tenant("Acme")
    payment_id = client.post("/api/payments", BODY, format="json").data["id"]

    first = client.post(f"/api/payments/{payment_id}/capture")
    assert first.status_code == 200
    assert first.data["status"] == "CAPTURED"

    from outbox.models import OutboxEvent

    events = OutboxEvent.objects.filter(aggregate_id=payment_id)
    assert events.count() == 1
    assert events.first().event_type == "PaymentCaptured"

    # Re-capturing is idempotent: still CAPTURED, still exactly one event.
    second = client.post(f"/api/payments/{payment_id}/capture")
    assert second.status_code == 200
    assert OutboxEvent.objects.filter(aggregate_id=payment_id).count() == 1
