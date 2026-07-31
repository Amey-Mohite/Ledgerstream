"""Root URL configuration for the Payment service."""

from django.contrib import admin
from django.urls import include, path

from core import health

urlpatterns = [
    # Liveness/readiness probes (unauthenticated — orchestrators call these).
    path("health/live", health.liveness, name="liveness"),
    path("health/ready", health.readiness, name="readiness"),
    # Auth: obtain/refresh JWT (the token carries the tenant_id claim).
    path("api/auth/", include("core.auth_urls")),
    # Domain API.
    path("api/", include("payments.urls")),
    path("admin/", admin.site.urls),
]
