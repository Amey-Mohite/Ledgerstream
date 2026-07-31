"""Payment API endpoints.

Every endpoint scopes data to `require_tenant_id(request)` — the tenant comes
from the signed JWT, so one tenant can never read or act on another's payments.
A missing/foreign id returns 404, never another tenant's record.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.tenancy import require_tenant_id

from .models import Payment
from .serializers import PaymentCreateSerializer, PaymentSerializer
from .services import authorize_payment, capture_payment, capture_payments_batch

IDEMPOTENCY_HEADER = "Idempotency-Key"
MAX_BATCH_CAPTURE = 100


class PaymentListCreateView(APIView):
    def get(self, request: Request) -> Response:
        tenant_id = require_tenant_id(request)
        payments = Payment.objects.for_tenant(tenant_id).order_by("-created_at")
        return Response(PaymentSerializer(payments, many=True).data)

    def post(self, request: Request) -> Response:
        tenant_id = require_tenant_id(request)
        payload = PaymentCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        payment, created = authorize_payment(
            tenant_id=tenant_id,
            amount_minor=payload.validated_data["amount_minor"],
            currency=payload.validated_data["currency"],
            reference=payload.validated_data["reference"],
            idempotency_key=request.headers.get(IDEMPOTENCY_HEADER),
        )
        # 201 for a fresh authorization, 200 when we replayed an idempotent hit.
        return Response(
            PaymentSerializer(payment).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PaymentDetailView(APIView):
    def get(self, request: Request, payment_id) -> Response:
        tenant_id = require_tenant_id(request)
        payment = get_object_or_404(
            Payment.objects.for_tenant(tenant_id), id=payment_id
        )
        return Response(PaymentSerializer(payment).data)


class PaymentBatchCaptureView(APIView):
    """Capture many payments in one request, with partial-success results.

    Body: {"payment_ids": ["<uuid>", ...]}. Returns 207 Multi-Status with a
    per-item `results` list (captured / already_captured / not_found /
    invalid_state). Each captured payment emits its own Kafka event → a great way
    to watch the event pipeline move a batch at once.
    """

    def post(self, request: Request) -> Response:
        tenant_id = require_tenant_id(request)
        payment_ids = request.data.get("payment_ids")

        if not isinstance(payment_ids, list) or not payment_ids:
            raise ValidationError("payment_ids must be a non-empty list.")
        if len(payment_ids) > MAX_BATCH_CAPTURE:
            raise ValidationError(
                f"Batch too large: {len(payment_ids)} > max {MAX_BATCH_CAPTURE}."
            )

        results = capture_payments_batch(
            tenant_id=tenant_id,
            payment_ids=payment_ids,
            correlation_id=getattr(request, "correlation_id", ""),
        )
        return Response({"results": results}, status=status.HTTP_207_MULTI_STATUS)


class PaymentCaptureView(APIView):
    def post(self, request: Request, payment_id) -> Response:
        tenant_id = require_tenant_id(request)
        # 404 first if it isn't this tenant's payment (isolation before action).
        get_object_or_404(Payment.objects.for_tenant(tenant_id), id=payment_id)

        payment, _emitted = capture_payment(
            tenant_id=tenant_id,
            payment_id=payment_id,
            correlation_id=getattr(request, "correlation_id", ""),
        )
        return Response(PaymentSerializer(payment).data, status=status.HTTP_200_OK)
