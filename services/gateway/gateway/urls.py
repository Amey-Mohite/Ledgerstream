"""Reverse-proxy routes: which path prefix goes to which backend.

Prefix matches (no trailing `$`), so `^api/payments` also covers
`/api/payments/<id>/capture`, and `^api/auth/` covers login + refresh.
"""

from django.urls import re_path

from .views import ProxyView

urlpatterns = [
    # Public: login / refresh (no token yet) → Payment.
    re_path(r"^api/auth/", ProxyView.as_view(service="payment", public=True)),
    # Writes to payments invalidate the tenant's cached balances (a capture changes them).
    re_path(r"^api/payments", ProxyView.as_view(service="payment", invalidates=("balances",))),
    # Balances are cache-aside'd per tenant (a hot, derived read).
    re_path(r"^api/balances", ProxyView.as_view(service="ledger", cache_name="balances")),
    re_path(r"^api/transactions", ProxyView.as_view(service="ledger")),
]
