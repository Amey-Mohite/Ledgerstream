"""Root URLs for the Gateway: health probes + the reverse-proxy routes."""

from django.urls import include, path

from core import health

urlpatterns = [
    path("health/live", health.liveness, name="liveness"),
    path("health/ready", health.readiness, name="readiness"),
    path("", include("gateway.urls")),
]
