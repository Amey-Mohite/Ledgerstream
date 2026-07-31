"""Reusable model bases."""

from __future__ import annotations

import uuid

from django.db import models


class UUIDModel(models.Model):
    """Primary key is a UUID, not a sequential int.

    Why: ids leak information (a sequential id lets anyone count your payments
    and guess neighbours). UUIDs are unguessable and safe to expose in URLs —
    important for a multi-tenant system where ids appear in API paths.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
