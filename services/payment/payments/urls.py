"""Payment API routes."""

from django.urls import path

from .views import PaymentCaptureView, PaymentDetailView, PaymentListCreateView

urlpatterns = [
    path("payments", PaymentListCreateView.as_view(), name="payment-list-create"),
    path("payments/<uuid:payment_id>", PaymentDetailView.as_view(), name="payment-detail"),
    path("payments/<uuid:payment_id>/capture", PaymentCaptureView.as_view(), name="payment-capture"),
]
