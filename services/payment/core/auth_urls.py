"""Auth endpoints: obtain and refresh JWTs."""

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from core.tokens import TenantTokenObtainPairView

urlpatterns = [
    path("token", TenantTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh", TokenRefreshView.as_view(), name="token_refresh"),
]
