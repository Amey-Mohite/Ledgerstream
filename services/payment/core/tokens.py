"""JWT issuance with the tenant claim baked in.

At login we embed the user's `tenant_id` into the access token. Every later
request then carries the tenant identity *inside a signed token* the client
cannot tamper with — the foundation of data-layer tenant isolation.
"""

from __future__ import annotations

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from core.tenancy import TENANT_CLAIM


class TenantTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        membership = getattr(user, "membership", None)
        if membership is not None:
            token[TENANT_CLAIM] = str(membership.tenant_id)
        return token


class TenantTokenObtainPairView(TokenObtainPairView):
    serializer_class = TenantTokenObtainPairSerializer
