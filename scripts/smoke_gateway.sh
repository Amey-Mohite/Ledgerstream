#!/usr/bin/env bash
#
# smoke_gateway.sh — exercise the whole platform THROUGH THE GATEWAY (Phase 4).
#
# Unlike smoke.sh (which hits Payment/Ledger directly), every call here goes to the
# gateway on :8010, so it exercises edge JWT auth, per-tenant rate limiting, the
# balances cache (X-Cache: MISS→HIT), the circuit breaker, and cursor pagination.
#
# Usage (Git Bash / WSL / macOS / Linux):
#     bash scripts/smoke_gateway.sh
#
# Override the base URL or credentials via env vars:
#     GW=http://localhost:8010 USERNAME=smoke PASSWORD=smokepw123 bash scripts/smoke_gateway.sh
#
# Prereqs (each in its own terminal):
#   - Gateway:  cd services/gateway && python manage.py runserver 127.0.0.1:8010
#   - Payment:  cd services/payment && python manage.py runserver 127.0.0.1:8000
#   - Ledger:   cd services/ledger  && python manage.py runserver 127.0.0.1:8021
#   - Workers (for ledger data + the saga step):
#       cd services/payment && python manage.py run_outbox_relay
#       cd services/ledger  && python manage.py consume_payments
#       cd services/payment && python manage.py consume_ledger_outcomes
#   - Redis reachable (REDIS_URL in .env; Upstash uses rediss://).
#   - A user exists (default smoke/smokepw123):
#       cd services/payment && python manage.py create_tenant --name T --username smoke --password smokepw123

set -euo pipefail

GW="${GW:-http://localhost:8010}"
USERNAME="${USERNAME:-smoke}"
PASSWORD="${PASSWORD:-smokepw123}"

json() { python -c "import sys,json;print(json.load(sys.stdin)$1)"; }
hr()   { echo "------------------------------------------------------------"; }
step() { echo; hr; echo "▶ $1"; hr; }

step "1. Health (readiness pings Redis)"
echo -n "gateway ready: "; curl -s "$GW/health/ready"; echo

step "2. Log in through the gateway (public route → Payment)"
LOGIN=$(curl -s -X POST "$GW/api/auth/token" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}")
ACCESS=$(echo "$LOGIN" | json "['access']" 2>/dev/null || true)
if [ -z "$ACCESS" ]; then echo "LOGIN FAILED: $LOGIN"; exit 1; fi
REFRESH=$(echo "$LOGIN" | json "['refresh']")
AUTH=(-H "Authorization: Bearer $ACCESS")
echo "access token acquired: ${ACCESS:0:40}..."

step "3. Create a payment (edge auth + idempotency)"
KEY="order-$$-$RANDOM"
CREATE=$(curl -s -X POST "$GW/api/payments" "${AUTH[@]}" \
  -H "Idempotency-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"amount_minor":1500,"currency":"usd","reference":"via-gateway"}')
echo "$CREATE"
PID=$(echo "$CREATE" | json "['id']")
echo "→ payment id: $PID"

step "4. Idempotency: same key again → same payment (HTTP 200, not 201)"
curl -s -o /dev/null -w "HTTP %{http_code} (expect 200)\n" \
  -X POST "$GW/api/payments" "${AUTH[@]}" \
  -H "Idempotency-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"amount_minor":1500,"currency":"usd","reference":"via-gateway"}'

step "5. Capture the payment (also invalidates the cached balances)"
curl -s -X POST "$GW/api/payments/$PID/capture" "${AUTH[@]}"; echo

step "6. Balances — first read is a cache MISS"
curl -si "$GW/api/balances" "${AUTH[@]}" | grep -iE "^HTTP|x-cache" || true

step "7. Balances again — cache HIT (served from Redis, Ledger not called)"
curl -si "$GW/api/balances" "${AUTH[@]}" | grep -iE "^HTTP|x-cache" || true

step "8. Transaction history — cursor-paginated"
TX=$(curl -s "$GW/api/transactions" "${AUTH[@]}")
echo "$TX" | python -m json.tool
# The backend's `next` URL points at the Ledger host; pull out just the cursor and
# follow it through the gateway.
CURSOR=$(echo "$TX" | python -c "import sys,json;from urllib.parse import urlparse,parse_qs;u=json.load(sys.stdin).get('next');print(parse_qs(urlparse(u).query).get('cursor',[''])[0] if u else '')")
if [ -n "$CURSOR" ]; then
  echo; echo "→ next page (cursor=${CURSOR:0:24}...):"
  curl -s "$GW/api/transactions?cursor=$CURSOR" "${AUTH[@]}" | python -m json.tool
else
  echo "→ only one page of history (no next cursor yet)."
fi

step "9. Saga failure path — GBP payment → ledger REJECTS → auto-VOID"
GKEY="gbp-$$-$RANDOM"
GID=$(curl -s -X POST "$GW/api/payments" "${AUTH[@]}" \
  -H "Idempotency-Key: $GKEY" -H "Content-Type: application/json" \
  -d '{"amount_minor":700,"currency":"gbp","reference":"saga"}' | json "['id']")
echo "→ created GBP payment $GID"
curl -s -o /dev/null -X POST "$GW/api/payments/$GID/capture" "${AUTH[@]}"
echo -n "waiting for the saga to compensate"
ST=""
for _ in $(seq 1 20); do
  ST=$(curl -s "$GW/api/payments/$GID" "${AUTH[@]}" | json "['status']")
  [ "$ST" = "VOIDED" ] && break
  echo -n "."; sleep 1
done
echo
[ "$ST" = "VOIDED" ] && echo "✅ payment auto-VOIDED by saga compensation" \
                      || echo "⚠️  status is '$ST' — is consume_ledger_outcomes running?"

step "10. Refresh the access token (no re-login)"
curl -s -X POST "$GW/api/auth/token/refresh" \
  -H "Content-Type: application/json" \
  -d "{\"refresh\":\"$REFRESH\"}" | json "['access'][:40]" | sed 's/^/new access (first 40): /'

step "11. Rate limiting — hammer past the token bucket to see 429s (runs LAST: it burns the budget)"
echo "status codes (expect 200s then 429s):"
for i in $(seq 1 70); do
  curl -s -o /dev/null -w "%{http_code} " "$GW/api/balances" "${AUTH[@]}"
done
echo
echo "(a throttled call also returns a Retry-After header)"

echo; hr; echo "✅ gateway smoke flow complete."; hr
