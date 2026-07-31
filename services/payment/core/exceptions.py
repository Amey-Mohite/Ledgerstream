"""Domain exceptions + a DRF exception handler that emits structured errors."""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class DomainError(Exception):
    """Base for business-rule violations that should surface as HTTP 4xx."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Invalid request."

    def __init__(self, detail: str | None = None):
        self.detail = detail or self.default_detail
        super().__init__(self.detail)


class InvalidStateTransition(DomainError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The payment is not in a state that allows this action."


def exception_handler(exc, context):
    """Render DomainError as JSON; defer everything else to DRF's default."""
    if isinstance(exc, DomainError):
        return Response({"error": exc.detail}, status=exc.status_code)
    return drf_exception_handler(exc, context)
