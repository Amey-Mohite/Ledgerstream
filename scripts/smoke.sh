#!/usr/bin/env bash
#
# smoke.sh — exercise the Ledgerstream Payment + Ledger APIs end to end.
#
# Runs the full flow: login → create payment → idempotency check → capture →
# (relay/consumer) → ledger balances + transactions. The SAME JWT works on both
# services (shared signing key).
#
# Usage (Git Bash / WSL / macOS / Linux):
#     bash scripts/smoke.sh
#
# Override anything via env vars:
#     PAY_URL=http://localhost:8000 LED_URL=http://localhost:8021 \
#     USERNAME=smoke PASSWORD=smokepw123 bash scripts/smoke.sh
#
# Prereqs:
#   - Payment server running:  cd services/payment && python manage.py runserver 127.0.0.1:8000
#   - Ledger server running:   cd services/ledger  && python manage.py runserver 127.0.0.1:8021
#   - For the ledger to show data, ALSO run the workers (each in its own terminal):
#       cd services/payment && python manage.py run_outbox_relay
#       cd services/ledger  && python manage.py consume_payments
#   - A user exists (default smoke/smokepw123). Create one with:
#       cd services/payment && python manage.py create_tenant --name T --username you --password pw

set -euo pipefail

PAY_URL="${PAY_URL:-http://localhost:8000}"
LED_URL="${LED_URL:-http://localhost:8021}"
USERNAME="${USERNAME:-smoke}"
PASSWORD="${PASSWORD:-smokepw123}"

# JSON field extractor (python is always available in this project's venv).
json() { python -c "import sys,json;print(json.load(sys.stdin)$1)"; }

hr() { echo "------------------------------------------------------------"; }
step() { echo; hr; echo "▶ $1"; hr; }

step "1. Health checks"
echo -n "payment ready: "; curl -s "$PAY_URL/health/ready"; echo
echo -n "ledger  ready: "; curl -s "$LED_URL/health/ready"; echo

step "2. Log in → get tokens"
LOGIN=$(curl -s -X POST "$PAY_URL/api/auth/token" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}")
ACCESS=$(echo "$LOGIN" | json "['access']" 2>/dev/null || true)
if [ -z "$ACCESS" ]; then echo "LOGIN FAILED: $LOGIN"; exit 1; fi
REFRESH=$(echo "$LOGIN" | json "['refresh']")
echo "access token acquired (first 40 chars): ${ACCESS:0:40}..."

AUTH=(-H "Authorization: Bearer $ACCESS")
KEY="order-$$-$RANDOM"   # a unique idempotency key for this run

step "3. Create a payment (Idempotency-Key: $KEY)"
CREATE=$(curl -s -X POST "$PAY_URL/api/payments" "${AUTH[@]}" \
  -H "Idempotency-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"amount_minor":1500,"currency":"usd","reference":"smoke-test"}')
echo "$CREATE"
PID=$(echo "$CREATE" | json "['id']")
echo "→ payment id: $PID"

step "4. Idempotency: repeat with the SAME key → same payment (200, not 201)"
curl -s -o /dev/null -w "HTTP %{http_code} (expect 200)\n" \
  -X POST "$PAY_URL/api/payments" "${AUTH[@]}" \
  -H "Idempotency-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"amount_minor":1500,"currency":"usd","reference":"smoke-test"}'

step "5. Get + list payments"
echo "GET one:"; curl -s "$PAY_URL/api/payments/$PID" "${AUTH[@]}"; echo
echo "LIST:";    curl -s "$PAY_URL/api/payments" "${AUTH[@]}"; echo

step "6. Capture the payment (writes the outbox event)"
curl -s -X POST "$PAY_URL/api/payments/$PID/capture" "${AUTH[@]}"; echo

step "6b. Batch capture — create 3 payments, capture them in ONE request"
BATCH_IDS=""
for i in 1 2 3; do
  BID=$(curl -s -X POST "$PAY_URL/api/payments" "${AUTH[@]}" \
    -H "Idempotency-Key: batch-$$-$i" -H "Content-Type: application/json" \
    -d '{"amount_minor":700,"currency":"usd","reference":"batch"}' | json "['id']")
  BATCH_IDS="$BATCH_IDS $BID"
done
echo "created:$BATCH_IDS"
BATCH_JSON=$(python -c "import json,sys;print(json.dumps({'payment_ids':sys.argv[1:]}))" $BATCH_IDS)
echo "POST /api/payments/capture (207 Multi-Status, per-item results):"
curl -s -X POST "$PAY_URL/api/payments/capture" "${AUTH[@]}" \
  -H "Content-Type: application/json" -d "$BATCH_JSON" | python -m json.tool
echo "→ each captured payment emits its own Kafka event (watch the relay + consumer)."

step "7. Ledger (needs the relay + consumer running to have data)"
echo "balances:";     curl -s "$LED_URL/api/balances" "${AUTH[@]}"; echo
echo "transactions:"; curl -s "$LED_URL/api/transactions" "${AUTH[@]}"; echo

step "8. Refresh the access token (no re-login)"
curl -s -X POST "$PAY_URL/api/auth/token/refresh" \
  -H "Content-Type: application/json" \
  -d "{\"refresh\":\"$REFRESH\"}" | json "['access'][:40]" | sed 's/^/new access (first 40): /'

echo; hr; echo "✅ smoke flow complete."; hr
