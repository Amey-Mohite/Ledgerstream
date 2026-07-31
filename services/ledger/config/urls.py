"""Root URLs for the Ledger service (read-only APIs + health)."""

from django.urls import include, path

from core import health

urlpatterns = [
    path("health/live", health.liveness, name="liveness"),
    path("health/ready", health.readiness, name="readiness"),
    path("api/", include("ledger.urls")),
]
