"""The reverse-proxy view.

One view class handles every proxied route; each URL pattern configures it via
`as_view(service=..., public=..., cache_name=..., invalidates=...)`. The gateway
mirrors the backends' URL scheme, so the downstream path is just `request.path`.

Resilience layered here, in order, per request:
  1. rate limit (429 if over budget)      — reject abuse early
  2. cache-aside on cacheable GETs (HIT → return without touching the backend)
  3. circuit-breaker-guarded proxy call    — fail fast / 503 on downstream trouble
  4. cache store (on GET miss) / invalidate (on writes)
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from core.tenancy import TENANT_CLAIM

from . import cache, client, ratelimit
from .breaker import CircuitOpen
from .client import DownstreamError


class ProxyView(APIView):
    service: str = ""              # "payment" | "ledger" — set per route
    public: bool = False           # auth endpoints are public (no token yet)
    cache_name: str = ""           # non-empty → cache-aside GETs under this name
    invalidates: tuple = ()        # cache names to drop after a successful write

    def get_permissions(self):
        # Which permission gate applies to THIS route (set via as_view(public=...)):
        #   public route  (self.public=True,  e.g. /api/auth/token) → [AllowAny()]
        #   protected     (self.public=False, e.g. /api/balances)   → [IsAuthenticated()]
        return [AllowAny()] if self.public else [IsAuthenticated()]

    # --- helpers --------------------------------------------------------------
    def _tenant(self, request) -> str | None:
        # request.auth is the VALIDATED JWT (a SimpleJWT token object), or None.
        #   authenticated request → token.get("tenant_id") → "tenant-123"
        #   public route / no token → getattr(...) is None → returns None
        token = getattr(request, "auth", None)
        return token.get(TENANT_CLAIM) if token else None

    def _rate_limit_identity(self, request) -> str:
        # The identity we bucket rate limits by (becomes the Redis key `rl:<identity>`):
        #   authed  GET /api/balances (tenant-123)          → "tenant:tenant-123"
        #   anon    POST /api/auth/token from 203.0.113.7   → "ip:203.0.113.7"
        tenant = self._tenant(request)
        if tenant:
            return f"tenant:{tenant}"           # authenticated → per tenant
        return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"   # login → per IP

    def _enforce_rate_limit(self, request) -> None:
        # ratelimit.check("tenant:tenant-123") → (allowed: bool, retry_after: float)
        #   under budget → (True, 0.0)   → returns None, request continues
        #   over budget  → (False, 1.8)  → raise Throttled(wait=2)
        #                                  → DRF renders HTTP 429 + header "Retry-After: 2"
        allowed, retry_after = ratelimit.check(self._rate_limit_identity(request))
        if not allowed:
            raise Throttled(wait=int(retry_after) + 1)   # DRF → 429 + Retry-After

    # --- the proxy ------------------------------------------------------------
    def _proxy(self, request) -> HttpResponse:
        self._enforce_rate_limit(request)
        tenant = self._tenant(request)

        # 2. cache-aside: serve a cacheable GET from Redis if present.
        if request.method == "GET" and self.cache_name and tenant:
            hit = cache.get(self.cache_name, tenant)
            if hit is not None:
                return HttpResponse(hit, content_type="application/json", headers={"X-Cache": "HIT"})

        # 3. breaker-guarded downstream call (fail fast / 503 on trouble).
        try:
            resp = client.forward(
                self.service,
                request.method,
                request.path,
                query_string=request.META.get("QUERY_STRING", ""),
                body=request.body,
                headers={k.lower(): v for k, v in request.headers.items()},
            )
        except (CircuitOpen, DownstreamError) as exc:
            # Graceful degradation: a clear 503, not a hung request or a stack trace.
            return JsonResponse(
                {"detail": f"{exc.service} temporarily unavailable"}, status=503
            )

        # 4. populate / invalidate the cache around the backend response.
        if request.method == "GET" and self.cache_name and tenant and resp.status_code == 200:
            cache.set(self.cache_name, tenant, resp.text, settings.CACHE_TTL)
        if request.method != "GET" and tenant and resp.status_code < 400:
            for name in self.invalidates:
                cache.invalidate(name, tenant)

        headers = {"X-Cache": "MISS"} if (request.method == "GET" and self.cache_name) else {}
        return HttpResponse(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get("content-type", "application/json"),
            headers=headers,
        )

    # Every verb funnels through the same transparent proxy.
    def get(self, request, *args, **kwargs):
        return self._proxy(request)

    def post(self, request, *args, **kwargs):
        return self._proxy(request)

    def put(self, request, *args, **kwargs):
        return self._proxy(request)

    def patch(self, request, *args, **kwargs):
        return self._proxy(request)

    def delete(self, request, *args, **kwargs):
        return self._proxy(request)
