"""Stateless JWT authentication at the EDGE — same pattern as the Ledger.

The gateway authenticates every protected request itself (reject bad traffic before
it reaches a backend), but it owns no users and never issued the tokens — they're
minted by the Payment service's login. All services share one key, `JWT_SIGNING_KEY`.

SimpleJWT's default `JWTAuthentication` verifies the token and then looks the user
up in a database by `user_id`. The gateway has no database, so we override
`get_user` to build a lightweight principal straight from the (already-verified)
token claims — no DB round-trip. `request.auth` still holds the full token, so the
`tenant_id` claim is available for logging/rate-limit keys.

Prod hardening: HS256 is symmetric (same key signs + verifies) → any key-holder can
mint tokens. Production would use RS256: the auth service signs with a private key,
everyone else verifies with the public key (check but not forge). Config, not code.
"""

from __future__ import annotations

from rest_framework_simplejwt.authentication import JWTAuthentication


class StatelessUser:
    """Minimal authenticated principal built from token claims (no DB row)."""

    is_authenticated = True   # so IsAuthenticated passes

    def __init__(self, user_id):
        self.id = user_id
        self.pk = user_id

    def __str__(self) -> str:
        return f"StatelessUser({self.id})"


class StatelessJWTAuthentication(JWTAuthentication):
    """JWTAuthentication that trusts the signed token instead of a user table."""

    def get_user(self, validated_token):
        # `validated_token` is already verified (signature + expiry) by the parent.
        # Build a principal from its claims — no `User.objects.get(...)`, so the
        # gateway needs no user table and no database at all.
        return StatelessUser(validated_token.get("user_id"))
