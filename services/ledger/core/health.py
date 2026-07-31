"""Liveness / readiness probes (same shape as the Payment service)."""

from __future__ import annotations

from django.db import connections
from django.http import JsonResponse


def liveness(request) -> JsonResponse:
    return JsonResponse({"status": "alive"})


def readiness(request) -> JsonResponse:
    ok = _database_ok()
    return JsonResponse(
        {"status": "ready" if ok else "not_ready", "checks": {"database": ok}},
        status=200 if ok else 503,
    )


def _database_ok() -> bool:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False
