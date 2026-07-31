"""Read-only Ledger API: balances + transaction history, tenant-scoped.

Balances are DERIVED (summed from the immutable lines), not stored-and-mutated —
the entries are the source of truth. Every query is scoped to the tenant from the
validated JWT.
"""

from __future__ import annotations

from django.db.models import Q, Sum
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.tenancy import require_tenant_id

from .models import Account, JournalEntry
from .serializers import JournalEntrySerializer


class BalancesView(APIView):
    def get(self, request: Request) -> Response:
        tenant_id = require_tenant_id(request)
        accounts = Account.objects.filter(tenant_id=tenant_id).annotate(
            debit_total=Sum("lines__amount_minor", filter=Q(lines__direction="DEBIT")),
            credit_total=Sum("lines__amount_minor", filter=Q(lines__direction="CREDIT")),
        )
        data = []
        for a in accounts:
            debit = a.debit_total or 0
            credit = a.credit_total or 0
            data.append(
                {
                    "code": a.code,
                    "type": a.type,
                    "normal_side": a.normal_side,
                    "debit_total": debit,
                    "credit_total": credit,
                    "balance": debit - credit,  # debit-normal signed balance
                }
            )
        return Response(data)


class TransactionsView(APIView):
    def get(self, request: Request) -> Response:
        tenant_id = require_tenant_id(request)
        entries = (
            JournalEntry.objects.filter(tenant_id=tenant_id)
            .prefetch_related("lines__account")
            .order_by("-created_at")[:100]
        )
        return Response(JournalEntrySerializer(entries, many=True).data)
