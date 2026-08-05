# Authentication, JWTs & Multi-Tenancy — the whole flow from scratch

> **In one sentence:** when a request arrives, the system has to answer two
> questions — **"who are you?"** (authentication) and **"which company's data are
> you allowed to touch?"** (tenancy) — and it answers both by reading a small signed
> token the client carries, without looking anything up in a database.

> 🧊 **In plain terms:** think of a conference. You show ID once at registration and
> get a **wristband** (the token). After that, every door just glances at your
> wristband — they don't re-check your passport, they trust the wristband because
> it's got a tamper-proof seal. The wristband also has a **company name** printed on
> it, so the "Acme booth" staff know you belong to Acme and won't let you rummage in
> Globex's drawers. That wristband is a JWT; the company name is your **tenant**.

This doc builds the whole picture bottom-up. If auth has always felt like magic,
start here and read top to bottom.

---

## 1. First: what is a "tenant"?

Ledgerstream is **multi-tenant**: one running system serves many separate customer
organizations at once. Each of those organizations is a **tenant**.

- A **tenant** = one merchant/business using the platform (e.g. "Acme Coffee").
- Many tenants share the **same** services and **same** databases.
- But their data is **isolated**: Acme must never see Globex's payments or ledger,
  even though both live in the same Postgres tables.

> 🧊 It's an **apartment building**. The building (the platform) has shared plumbing,
> wiring, and a single front structure — but each apartment (tenant) is private and
> locked. You get a key to *your* apartment only.

The thing that enforces this is a single value — the **`tenant_id`** — attached to
every row and checked on every query. "User" and "tenant" are different: a **user**
is a person who logs in; a **tenant** is the organization they belong to. In
Ledgerstream one user belongs to one tenant (kept simple), and the user's tenant is
stamped into their token at login.

---

## 2. The building block: what a JWT actually is

A **JWT** (JSON Web Token) is just a string with **three parts** separated by dots:

```
eyJhbGciOiJIUzI1NiJ9  .  eyJ1c2VyX2lkIjoiMSIsInRlbmFudF9pZCI6InQtYWJjIiwiZXhwIjoxNzM1Njg5NjAwfQ  .  3vQ2r…signature
      HEADER                                   PAYLOAD (the claims)                                        SIGNATURE
```

Decode the middle part (it's just base64, **not encryption**) and you get the
**claims** — plain JSON:

```json
{
  "token_type": "access",
  "user_id": "1",
  "tenant_id": "t-abc",     ← the tenant, carried in the token
  "exp": 1735689600,        ← expiry (unix time)
  "jti": "9f2c…"            ← unique token id
}
```

Two things people get wrong:

- **A JWT is not encrypted — it's signed.** Anyone can read the claims (they're
  base64, not secret). What they *can't* do is **change** them. That's the signature.
- **The signature is the whole point.** It's computed as
  `HMAC-SHA256(header + "." + payload, SECRET_KEY)`. If someone flips `tenant_id` to
  `t-globex`, the signature no longer matches, and any server that knows the secret
  rejects it. So the token is **tamper-evident**: readable, but unforgeable without
  the key.

> 🧊 The wristband analogy again: the printed info (claims) is visible to everyone,
> but the **hologram seal** (signature) can only be made by the registration desk
> (whoever holds the secret key). A fake wristband has no valid hologram.

---

## 3. The login flow — how you get a token

You authenticate **once** (with a password), and from then on you carry the token.

```mermaid
sequenceDiagram
  participant U as Client
  participant P as Payment /api/auth/token
  participant DB as Users DB
  U->>P: POST /api/auth/token {username, password}
  P->>DB: look up user, check password hash
  DB-->>P: ok, user #1 belongs to tenant t-abc
  P->>P: mint tokens signed with JWT_SIGNING_KEY,<br/>stamping user_id + tenant_id into the claims
  P-->>U: access JWT (~30min) + refresh JWT (~1 day)
```

The **login endpoint is the only place** that touches the user database and checks a
password. It's also the only place that **mints** tokens. Notice it stamps the
`tenant_id` into the token *here* — so every later request carries the tenant without
anyone having to look it up again.

### Why TWO tokens (access + refresh)?

| | **Access token** | **Refresh token** |
|---|---|---|
| Lifetime | short (~30 min) | long (~1 day) |
| Sent on | *every* API request | only to `/api/auth/token/refresh` |
| Purpose | prove who you are, fast | get a new access token without re-login |

The trade-off is **security vs convenience**. The access token is used constantly, so
if it leaks you want it to expire soon. But making users log in every 30 minutes is
awful — so a longer-lived refresh token can mint fresh access tokens. If the access
token expires, the client calls **`/api/auth/token/refresh`** with the refresh token
and gets a new access token. Only the refresh endpoint accepts a refresh token, so the
long-lived credential travels rarely.

---

## 4. Using the token — how a request is authenticated

On every subsequent request, the client sends the access token in the
**`Authorization`** header:

```
GET /api/balances
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lk…
```

The server then, **with no database lookup**:

```mermaid
flowchart TD
  A["request with<br/>Authorization: Bearer JWT"] --> B["recompute the signature<br/>with JWT_SIGNING_KEY"]
  B --> C{"signature<br/>matches?"}
  C -->|"no — forged/altered"| X["401 Unauthorized"]
  C -->|"yes"| D{"exp in the<br/>future?"}
  D -->|"no — expired"| X
  D -->|"yes"| E["trust the claims:<br/>user_id, tenant_id"]
  E --> F["request.auth = the token<br/>request.user = principal"]
```

That's it. **Verifying a token is pure math** (recompute the signature, compare;
check `exp`). No `SELECT * FROM users`. This is what "**stateless** auth" means and
why it scales: any server that holds the signing key can validate any token on its
own, instantly.

In this codebase that check is `StatelessJWTAuthentication` (`core/authentication.py`):
SimpleJWT verifies the signature + expiry, and we override `get_user` to build a
lightweight principal straight from the claims (no user table needed on the Ledger or
Gateway — they never issued the token and don't own the users).

**The exact function chain** (which function does the crypto):

```
DRF calls  StatelessJWTAuthentication.authenticate(request)
  → get_header(request)            # pull the Authorization header
  → get_raw_token(header)          # → the "eyJ…" token string
  → get_validated_token(raw_token) # constructs the token object:
        AccessToken(raw_token)
          → TokenBackend.decode(...)
              → jwt.decode(token, KEY, algorithms=["HS256"])   ← recompute HMAC,
                                                                 compare, check exp
          → Token.verify()          # token_type + exp claim checks
  → get_user(validated_token)      # → StatelessUser (no DB)
  ⇒ request.user, request.auth = the validated token
```

The single line **`jwt.decode(token, KEY, ["HS256"])`** (PyJWT, under SimpleJWT) is
where "verify the signature" physically happens: it recomputes
`HMAC_SHA256(header.payload, KEY)` and constant-time-compares it to the token's
signature, then checks `exp` — raising `InvalidToken` (→ DRF `401`) if either fails.
Afterwards `require_tenant_id(request)` just reads `request.auth["tenant_id"]`, which
is now trustworthy *because* the signature passed. The full request lifecycle with
every function — auth, rate limit, cache, breaker, proxy — is in
[phase4.md Part 2.5](../phase4.md).

---

## 5. Service-to-service trust (the part that clicks late)

Here's the elegant bit. **Payment mints the token. Ledger and Gateway validate it —
but they have no users table.** How can the Ledger trust a token it never issued?

Because all three services **share one secret**, `JWT_SIGNING_KEY`. With HS256, the
same key both signs *and* verifies. So:

- Payment signs the token with the key at login.
- The Ledger/Gateway hold the same key, so they can **recompute the signature** and
  confirm the token is authentic — the signature *is* the proof, not a shared user
  database.

```mermaid
flowchart LR
  P["Payment<br/>(mints tokens)"] -->|"signs with KEY"| T(["JWT"])
  T -->|"presented to"| G["Gateway"]
  T -->|"presented to"| L["Ledger"]
  G -->|"verifies with the SAME KEY"| OK1["trusted ✅"]
  L -->|"verifies with the SAME KEY"| OK2["trusted ✅"]
```

> **Prod hardening (interview-worthy):** HS256 is **symmetric** — the same key signs
> and verifies, so *every* service holding it could also *mint* tokens. A leak of any
> service leaks the ability to forge auth. Production uses **RS256** (asymmetric): the
> auth service signs with a **private** key; everyone else verifies with the **public**
> key — they can *check* tokens but never *forge* them. It's a config change
> (SIGNING_KEY/VERIFYING_KEY + ALGORITHM), not a code change.

---

## 6. Tenancy in practice — scoping every query

Authentication answered "who are you". Now **authorization by tenant**: every data
access is filtered by the `tenant_id` from the token, so a query *cannot* return
another tenant's rows.

```python
# core/tenancy.py
def require_tenant_id(request) -> str:
    token = getattr(request, "auth", None)         # the validated JWT
    tenant_id = token["tenant_id"] if token else None   # e.g. "t-abc"
    if not tenant_id:
        raise PermissionDenied(...)                # 403 — never run an unscoped query
    return tenant_id

# in a view:
tenant_id = require_tenant_id(request)             # "t-abc"
JournalEntry.objects.filter(tenant_id=tenant_id)   # ← THE isolation choke point
```

The critical rule: the `tenant_id` comes from the **verified token**, never from a
client-supplied header or body (which could be forged). Because the token is signed,
its `tenant_id` is trustworthy. Every `.filter(tenant_id=…)` is the line that keeps
Acme out of Globex's data.

---

## 7. Where the gateway fits (edge auth + defense in depth)

In Phase 4 the **gateway** sits in front and validates the token **at the edge** —
rejecting anonymous traffic before it ever reaches a backend. The backends *also*
validate it. That's **defense in depth**: two independent checks, and because
validation is stateless (just the shared key), doing it twice is cheap.

```mermaid
sequenceDiagram
  participant C as Client
  participant G as Gateway
  participant L as Ledger
  C->>G: GET /api/balances (Bearer JWT)
  G->>G: verify signature+exp (edge auth), read tenant_id
  G->>L: forward the request (same Bearer token)
  L->>L: verify again, require_tenant_id → "t-abc"
  L->>L: SELECT … WHERE tenant_id = 't-abc'
  L-->>G: balances for t-abc only
  G-->>C: 200 balances
```

The **same token works on every hop** — that's the payoff of a shared signing key and
stateless validation.

---

## 8. Interview questions you should be able to answer

- *Is a JWT encrypted?* → No — signed. The claims are base64 (readable); the signature
  makes them tamper-evident. Don't put secrets in a JWT.
- *How is a token verified without a DB?* → Recompute the HMAC signature with the
  shared key and compare, then check `exp`. Pure computation → stateless, scalable.
- *Access vs refresh token — why two?* → Short-lived access (limits leak damage, sent
  everywhere) + long-lived refresh (avoids constant re-login, sent only to the refresh
  endpoint).
- *How does a service trust a token it didn't issue?* → It shares the signing key; the
  signature is the trust anchor, not a shared user table (database-per-service).
- *HS256 vs RS256?* → Symmetric (same key signs+verifies; any holder can forge) vs
  asymmetric (private signs, public verifies; verifiers can't forge). Prod → RS256.
- *What is a tenant, and how is isolation enforced?* → A customer organization sharing
  the platform; every query is filtered by the `tenant_id` claim from the verified
  token, so data can't cross tenants.
- *Why read tenant_id from the token, not a header?* → A header is client-controlled
  and forgeable; the signed token's claim is trustworthy.
- *What is stateless authentication and why does it scale?* → No per-request session/DB
  lookup — any server with the key validates any token alone, so you can add servers
  freely.

---

## 9. How Ledgerstream uses it

**Payment** owns the users and the **login** (`/api/auth/token`): it checks the
password and mints **access + refresh** JWTs signed with `JWT_SIGNING_KEY`, stamping
`user_id` + `tenant_id` into the claims. **Ledger** and **Gateway** are stateless
validators (`StatelessJWTAuthentication`): they verify the shared-key signature + expiry
and read the claims with **no user table**. Every data query is scoped with
`require_tenant_id(request)` → `.filter(tenant_id=…)` — the tenant-isolation choke
point. The **gateway** adds **edge auth** (reject before a backend is hit) while the
backends re-validate (defense in depth); one token works across every hop. Symmetric
HS256 today; RS256 is the documented prod upgrade. See also
[multi-tenancy.md](multi-tenancy.md) (isolation depth) and the Phase 1 walkthrough
[docs/phase1.md](../phase1.md).
