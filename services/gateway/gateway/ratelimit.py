"""Per-identity **token-bucket** rate limiting, backed by Redis.

Why a token bucket (not a fixed window): it allows short **bursts** up to `capacity`
while capping the **sustained** rate at `refill_per_sec` — and it doesn't have the
fixed-window edge problem (2× the limit across a window boundary). Each identity
(a tenant, or a client IP for anonymous login traffic) gets its own bucket.

Why a Lua script: the check is read-modify-write (read tokens → refill → maybe spend
→ write back). Doing that as separate GET/SET commands has a race between concurrent
requests. Redis runs a Lua script **atomically** on the server, so the whole
bucket update is one indivisible step — correct under concurrency, one round-trip.
"""

from __future__ import annotations

import time

from django.conf import settings

from gateway.redis_conn import get_redis

# KEYS[1]=bucket, ARGV: capacity, refill_per_sec, now, requested.
# Returns {allowed(0|1), retry_after_seconds(string)}.
# Worked example — capacity=60, refill=1/s, the bucket for "tenant:tenant-123":
#   1st ever call: no state → tokens=60 → spend 1 → tokens=59 → return {1, "0"}
#   rapid calls:   … tokens 59,58,…,1,0 …
#   61st call in the same second: tokens=0, want=1 → deny
#       retry_after = (1 - 0) / 1 = 1.0s → return {0, "1"}
#   2s idle then a call: refill = 2s * 1/s = +2 tokens → allowed again → {1, "0"}
_TOKEN_BUCKET_LUA = """
local key      = KEYS[1]         -- e.g. "rl:tenant:tenant-123"
local capacity = tonumber(ARGV[1])   -- 60  (max burst)
local refill   = tonumber(ARGV[2])   -- 1.0 (tokens added per second)
local now      = tonumber(ARGV[3])   -- 1735689600.42 (unix time, from Python)
local want     = tonumber(ARGV[4])   -- 1   (tokens this request costs)

local state  = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts     = tonumber(state[2])
if tokens == nil then          -- first request for this identity → full bucket
  tokens = capacity
  ts     = now
end

tokens = math.min(capacity, tokens + math.max(0, now - ts) * refill)  -- refill

local allowed = 0
local retry_after = 0
if tokens >= want then
  tokens = tokens - want
  allowed = 1
else
  retry_after = (want - tokens) / refill     -- seconds until enough tokens
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
-- Let an idle bucket expire so we don't leak keys for one-off callers.
redis.call('EXPIRE', key, math.ceil(capacity / refill) + 1)
return {allowed, tostring(retry_after)}
"""

_script = None


def check(identity: str) -> tuple[bool, float]:
    """Spend one token for `identity`. Returns (allowed, retry_after_seconds).

    Example:
      check("tenant:tenant-123") → (True, 0.0)    # had tokens → allowed
      check("tenant:tenant-123") → (False, 1.0)   # bucket empty → wait ~1s
    """
    r = get_redis()
    global _script
    if _script is None:
        _script = r.register_script(_TOKEN_BUCKET_LUA)   # cached by SHA on the server

    # The Lua returns e.g. [1, "0"]  (allowed) or [0, "1.8"]  (denied, wait 1.8s).
    allowed, retry_after = _script(
        keys=[f"rl:{identity}"],
        args=[settings.RATE_LIMIT_CAPACITY, settings.RATE_LIMIT_REFILL_PER_SEC, time.time(), 1],
    )
    return bool(int(allowed)), float(retry_after)   # (True, 0.0) / (False, 1.8)
