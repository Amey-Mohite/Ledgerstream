"""The immutable double-entry ledger.

Three tables:
- **Account** — a bucket money sits in, scoped to a tenant (e.g. CASH,
  MERCHANT_PAYABLE). Each has a normal balance side (assets are debit-normal,
  liabilities are credit-normal).
- **JournalEntry** — one balanced transaction (Σ debits == Σ credits). Its
  `event_id` is UNIQUE, which is what makes consuming the same Kafka event twice a
  no-op (idempotency at the data layer).
- **LedgerLine** — one debit or credit line of an entry, against one account.

Immutability: this is an append-only audit log. We never UPDATE or DELETE a
posted line — a correction is a NEW reversing entry. The app never exposes
mutation; at higher stakes you'd also enforce it with DB triggers / revoked
UPDATE+DELETE grants.
"""

from __future__ import annotations

from django.db import models

from core.models import TimestampedModel, UUIDModel


class Account(UUIDModel, TimestampedModel):
    class Type(models.TextChoices):
        ASSET = "ASSET", "Asset"            # debit-normal (balance = debits - credits)
        LIABILITY = "LIABILITY", "Liability"  # credit-normal (balance = credits - debits)
        REVENUE = "REVENUE", "Revenue"
        EXPENSE = "EXPENSE", "Expense"

    tenant_id = models.UUIDField()
    code = models.CharField(max_length=64)          # e.g. "CASH", "MERCHANT_PAYABLE"
    type = models.CharField(max_length=16, choices=Type.choices)

    class Meta:
        db_table = "account"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "code"], name="uniq_tenant_account_code"
            )
        ]

    @property
    def normal_side(self) -> str:
        return "DEBIT" if self.type in (self.Type.ASSET, self.Type.EXPENSE) else "CREDIT"

    def __str__(self) -> str:
        return f"{self.code} ({self.tenant_id})"


class JournalEntry(UUIDModel, TimestampedModel):
    tenant_id = models.UUIDField()
    # The source event's id. UNIQUE → one entry per event → idempotent consumer.
    event_id = models.UUIDField(unique=True)
    description = models.CharField(max_length=255, blank=True, default="")
    occurred_at = models.DateTimeField()
    correlation_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "journal_entry"
        indexes = [
            models.Index(fields=["tenant_id", "created_at"], name="journal_tenant_idx"),
        ]

    def __str__(self) -> str:
        return f"JournalEntry {self.id} ({self.tenant_id})"


class LedgerLine(UUIDModel, TimestampedModel):
    class Direction(models.TextChoices):
        DEBIT = "DEBIT", "Debit"
        CREDIT = "CREDIT", "Credit"

    entry = models.ForeignKey(
        JournalEntry, on_delete=models.PROTECT, related_name="lines"
    )
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    direction = models.CharField(max_length=6, choices=Direction.choices)
    amount_minor = models.BigIntegerField()   # positive integer, minor units
    currency = models.CharField(max_length=3)

    class Meta:
        db_table = "ledger_line"
        indexes = [
            models.Index(fields=["account", "created_at"], name="line_account_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.direction} {self.amount_minor} {self.currency} → {self.account.code}"
