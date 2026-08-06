#!/usr/bin/env bash
#
# smoke_ai.sh — exercise the AI Query service (Phase 6): AI -> Gateway -> Ledger.
#
# The AI service (:8030) does edge JWT auth, a per-tenant LLM rate limit, then runs
# the LLM tool-use loop. The tenant-scoped read tools call the SAME gateway (:8010)
# every other client uses, keyed by the caller's JWT — so a "balance" question walks
# AI(8030) -> Gateway(8010) -> Ledger, and the answer is grounded in real ledger data.
#
# Usage (Git Bash / WSL / macOS / Linux):
#     bash scripts/smoke_ai.sh
#
# Override endpoints / credentials via env vars:
#     AI=http://localhost:8030 GW=http://localhost:8010 \
#     USERNAME=smoke PASSWORD=smokepw123 bash scripts/smoke_ai.sh
#
# Bring your own token (skips the gateway login — e.g. an offline/mock run):
#     TOKEN=eyJhbGci... bash scripts/smoke_ai.sh
#
# Prereqs:
#   - AI service:  cd services/ai && uvicorn app.main:app --port 8030
#   - For grounded answers (steps 4-5), the rest of the chain must be up + seeded:
#       Gateway:  cd services/gateway && python manage.py runserver 127.0.0.1:8010
#       Ledger:   cd services/ledger  && python manage.py runserver 127.0.0.1:8021
#       Payment:  cd services/payment && python manage.py runserver 127.0.0.1:8000
#       Workers:  run_outbox_relay / consume_payments / consume_ledger_outcomes
#       A seeded user (default smoke/smokepw123):
#         cd services/payment && python manage.py create_tenant --name T --username smoke --password smokepw123
#         cd services/payment && python manage.py seed        # gives the tenant some ledger history
#   - OFFLINE mode: start the AI service with LLM_PROVIDER_ORDER=mock to answer with
#     no API keys / no network. A "balance" question still needs the gateway+ledger up
#     (the mock provider drives the same tool loop); a non-ledger question (step 3) does not.

set -euo pipefail

AI="${AI:-http://localhost:8030}"
GW="${GW:-http://localhost:8010}"
USERNAME="${USERNAME:-smoke}"
PASSWORD="${PASSWORD:-smokepw123}"

json() { python -c "import sys,json;print(json.load(sys.stdin)$1)"; }
hr()   { echo "------------------------------------------------------------"; }
step() { echo; hr; echo "▶ $1"; hr; }

# One question -> pretty-print {answer, provider, tools_used}.
ask() {
  local q="$1"
  curl -s -X POST "$AI/api/ai/query" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "{\"question\":$(python -c "import json,sys;print(json.dumps(sys.argv[1]))" "$q")}" \
    | python -m json.tool
}

step "1. Health"
echo -n "ai live:  "; curl -s "$AI/health/live";  echo
echo -n "ai ready: "; curl -s "$AI/health/ready"; echo

step "2. Get a tenant-scoped token"
if [ -n "${TOKEN:-}" ]; then
  echo "using TOKEN from environment: ${TOKEN:0:40}..."
else
  echo "logging in through the gateway ($GW/api/auth/token)..."
  LOGIN=$(curl -s -X POST "$GW/api/auth/token" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}")
  TOKEN=$(echo "$LOGIN" | json "['access']" 2>/dev/null || true)
  if [ -z "$TOKEN" ]; then
    echo "LOGIN FAILED: $LOGIN"
    echo "(tip: pass your own token instead — TOKEN=... bash scripts/smoke_ai.sh)"
    exit 1
  fi
  echo "access token acquired: ${TOKEN:0:40}..."
fi

step "3. A non-ledger question — no tool call (tests auth + LLM path only)"
echo "expect tools_used: []"
ask "hello, what can you do?"

step "4. A balance question — model calls get_balances via the gateway"
echo "expect tools_used: [\"get_balances\"] and a grounded \$ figure"
ask "what is my cash balance?"

step "5. A history question — model calls get_transactions via the gateway"
echo "expect tools_used: [\"get_transactions\"]"
ask "show me my recent transactions"

step "6. Auth guard — no token → 401"
curl -s -o /dev/null -w "HTTP %{http_code} (expect 401)\n" \
  -X POST "$AI/api/ai/query" -H "Content-Type: application/json" \
  -d '{"question":"hi"}'

step "7. Per-tenant LLM rate limit — burst past the bucket to see 429s (runs LAST)"
echo "status codes (expect 200s then 429s — bucket is ~10 burst):"
for _ in $(seq 1 15); do
  curl -s -o /dev/null -w "%{http_code} " \
    -X POST "$AI/api/ai/query" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"question":"hello"}'
done
echo

echo; hr; echo "✅ AI smoke flow complete."; hr
