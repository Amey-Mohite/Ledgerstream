"""Payment API routes."""

from django.urls import path

from .views import (
    PaymentBatchCaptureView,
    PaymentCaptureView,
    PaymentDetailView,
    PaymentListCreateView,
)

urlpatterns = [
    path("payments", PaymentListCreateView.as_view(), name="payment-list-create"),
    # Literal "capture" must precede the <uuid> route so it isn't shadowed.
    path("payments/capture", PaymentBatchCaptureView.as_view(), name="payment-batch-capture"),
    path("payments/<uuid:payment_id>", PaymentDetailView.as_view(), name="payment-detail"),
    path("payments/<uuid:payment_id>/capture", PaymentCaptureView.as_view(), name="payment-capture"),
]
