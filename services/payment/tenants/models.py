"""Tenant + Membership.

A **Tenant** is a merchant/organization — the isolation boundary. A **Membership**
links a Django auth user to exactly one tenant, so that when the user logs in we
can stamp their `tenant_id` into the JWT (see core/tokens.py).

Keeping auth (Django's User) and tenancy (Membership) separate means we don't
need a custom user model just to add one field — a smaller, less risky design.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import TimestampedModel, UUIDModel


class Tenant(UUIDModel, TimestampedModel):
    name = models.CharField(max_length=200)

    class Meta:
        db_table = "tenant"

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"


class Membership(UUIDModel, TimestampedModel):
    """Which tenant a user belongs to. One tenant per user (kept simple)."""

    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        MEMBER = "MEMBER", "Member"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="membership",
    )
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)

    class Meta:
        db_table = "membership"

    def __str__(self) -> str:
        return f"{self.user} @ {self.tenant}"
