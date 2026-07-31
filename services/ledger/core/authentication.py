"""Stateless JWT authentication — service-to-service trust.

WHY THIS EXISTS
---------------
The Ledger service must authenticate API callers, but it does NOT own users and
never issued their tokens — the tokens are minted by the *Payment* service's login
(`/api/auth/token`). Both services share one secret, `JWT_SIGNING_KEY`.

SimpleJWT's default `JWTAuthentication` does two things: verify the token, then
**look the user up in the database** by the `user_id` claim. That second step
would FAIL here — the Ledger's database has no user table (those rows live in the
Payment service). So we override `get_user` to skip the DB and build a lightweight
principal straight from the (already-verified) token claims.

The result — a downstream service trusting an upstream one's tokens:
  1. verify the signature with the shared key   → proves the token is authentic
  2. check expiry                               → proves it's still valid
  3. read the `tenant_id` claim                 → for tenant scoping
  4. return, WITHOUT any database/user lookup    → stateless, no shared user store

WHY IT'S DESIGNED THIS WAY
  * database-per-service: the Ledger doesn't replicate Payment's users; the
    *signature* is the trust anchor, not a shared table.
  * stateless: no DB round-trip per request just to authenticate.
  * decoupled: the two services share only a signing key, not a user store.

PRODUCTION HARDENING (interview-worthy)
  We use HS256 — a SYMMETRIC key: the same secret both signs AND verifies. That
  means every service holding the key could also MINT tokens, so a compromise of
  any service is a compromise of auth. In production you'd switch to an ASYMMETRIC
  algorithm (RS256): the auth service signs with a PRIVATE key; every other service
  verifies with the PUBLIC key — they can *check* tokens but cannot *forge* them.
  That's a config change (SIGNING_KEY/VERIFYING_KEY + ALGORITHM), not a code change.
"""

from __future__ import annotations

from rest_framework_simplejwt.authentication import JWTAuthentication


class StatelessUser:
    """A minimal authenticated principal built from token claims (no DB row).

    DRF requires the authenticator to return a user object; permissions like
    IsAuthenticated only check `.is_authenticated`. We don't need a real user
    record — just something truthy that carries the id from the token.
    """

    is_authenticated = True   # so IsAuthenticated passes

    def __init__(self, user_id):
        self.id = user_id     # from the token's "user_id" claim (for auditing/logs)
        self.pk = user_id

    def __str__(self) -> str:
        return f"StatelessUser({self.id})"


class StatelessJWTAuthentication(JWTAuthentication):
    """JWTAuthentication that trusts the signed token instead of a user table."""

    def get_user(self, validated_token):
        # `validated_token` is ALREADY verified by the parent class (signature +
        # expiry checked against JWT_SIGNING_KEY). We just build a principal from
        # its claims — no `User.objects.get(...)`, so no user table is required.
        # `request.auth` still holds the full token, so views read `tenant_id`
        # from it via core.tenancy.require_tenant_id().
        return StatelessUser(validated_token.get("user_id"))
