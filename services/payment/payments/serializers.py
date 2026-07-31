"""Request/response shapes for the Payment API."""

from __future__ import annotations

from rest_framework import serializers

from .models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    """Read shape — what the API returns."""

    class Meta:
        model = Payment
        fields = [
            "id",
            "amount_minor",
            "currency",
            "reference",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PaymentCreateSerializer(serializers.Serializer):
    """Write shape — what a client sends to authorize a payment."""

    amount_minor = serializers.IntegerField(min_value=1)
    currency = serializers.CharField(min_length=3, max_length=3)
    reference = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")

    def validate_currency(self, value: str) -> str:
        return value.upper()
