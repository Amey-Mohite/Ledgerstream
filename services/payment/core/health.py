"""Liveness and readiness probes.

Two DIFFERENT checks (see docs/concepts/health-checks-liveness-readiness.md):

- liveness  — "is the process alive?" Deliberately dumb: it must NOT touch the
  database, or a DB blip would make the orchestrator restart every instance.
- readiness — "can I serve requests right now?" Checks the dependencies we can't
  serve without (the database). On failure the orchestrator routes traffic away
  but does not restart us.
"""

from __future__ import annotations

from django.db import connections
from django.http import JsonResponse


def liveness(request) -> JsonResponse:
    return JsonResponse({"status": "alive"})


def readiness(request) -> JsonResponse:
    checks = {"database": _database_ok()}
    ready = all(checks.values())
    return JsonResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status=200 if ready else 503,
    )


def _database_ok() -> bool:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 — any failure means "not ready"
        return False
