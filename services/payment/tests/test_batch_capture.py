"""Batch capture — partial success + idempotency + one outbox event per payment."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.django_db

BODY = {"amount_minor": 500, "currency": "USD"}


def _create(client):
    return client.post("/api/payments", BODY, format="json").data["id"]


def test_batch_capture_partial_success(make_tenant):
    _tenant, client = make_tenant("Acme")
    p1, p2 = _create(client), _create(client)
    missing = str(uuid.uuid4())               # a valid-but-unknown id

    resp = client.post(
        "/api/payments/capture",
        {"payment_ids": [p1, p2, missing]},
        format="json",
    )
    assert resp.status_code == 207            # Multi-Status
    by_id = {r["payment_id"]: r for r in resp.data["results"]}

    assert by_id[p1]["outcome"] == "captured"
    assert by_id[p1]["status"] == "CAPTURED"
    assert by_id[p2]["outcome"] == "captured"
    assert by_id[missing]["outcome"] == "not_found"   # one bad id doesn't fail the batch

    # Exactly one outbox event per captured payment.
    from outbox.models import OutboxEvent
    assert OutboxEvent.objects.filter(aggregate_id__in=[p1, p2]).count() == 2


def test_batch_capture_is_idempotent(make_tenant):
    _tenant, client = make_tenant("Acme")
    p1 = _create(client)

    first = client.post("/api/payments/capture", {"payment_ids": [p1]}, format="json")
    second = client.post("/api/payments/capture", {"payment_ids": [p1]}, format="json")

    assert first.data["results"][0]["outcome"] == "captured"
    assert second.data["results"][0]["outcome"] == "already_captured"

    from outbox.models import OutboxEvent
    assert OutboxEvent.objects.filter(aggregate_id=p1).count() == 1   # no duplicate event


def test_batch_capture_validates_input(make_tenant):
    _tenant, client = make_tenant("Acme")
    assert client.post("/api/payments/capture", {}, format="json").status_code == 400
    assert client.post("/api/payments/capture", {"payment_ids": []}, format="json").status_code == 400


def test_batch_capture_is_tenant_scoped(make_tenant):
    _a, client_a = make_tenant("A")
    _b, client_b = make_tenant("B")
    p_a = _create(client_a)

    # B tries to capture A's payment → not_found, and A's payment stays AUTHORIZED.
    resp = client_b.post("/api/payments/capture", {"payment_ids": [p_a]}, format="json")
    assert resp.data["results"][0]["outcome"] == "not_found"

    from payments.models import Payment
    assert Payment.objects.get(id=p_a).status == Payment.Status.AUTHORIZED


def test_batch_capture_rejects_oversized_batch(make_tenant):
    _tenant, client = make_tenant("Acme")
    import uuid
    too_many = [str(uuid.uuid4()) for _ in range(101)]   # MAX is 100
    resp = client.post("/api/payments/capture", {"payment_ids": too_many}, format="json")
    assert resp.status_code == 400
