"""Read shapes for the Ledger API."""

from __future__ import annotations

from rest_framework import serializers

from .models import JournalEntry, LedgerLine


class LedgerLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code")

    class Meta:
        model = LedgerLine
        fields = ["direction", "amount_minor", "currency", "account_code"]


class JournalEntrySerializer(serializers.ModelSerializer):
    lines = LedgerLineSerializer(many=True)

    class Meta:
        model = JournalEntry
        fields = ["id", "event_id", "description", "occurred_at", "created_at", "lines"]
