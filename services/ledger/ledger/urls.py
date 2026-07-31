"""Ledger read API routes."""

from django.urls import path

from .views import BalancesView, TransactionsView

urlpatterns = [
    path("balances", BalancesView.as_view(), name="balances"),
    path("transactions", TransactionsView.as_view(), name="transactions"),
]
