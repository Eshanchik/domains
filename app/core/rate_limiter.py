"""Centralized rate limiting for external services (SPEC §5, NFR-3).

Two Redis-backed primitives:
- a **token bucket** (smooth per-second rate with burst capacity), used per service
  and per-TLD (WHOIS), and
- a **daily budget** counter (e.g. VirusTotal free: 500/day).

Both are atomic (Lua) so they behave correctly under concurrent workers.
"""

from __future__ import annotations

import time

import redis.asyncio as aioredis

# Atomic token-bucket refill+consume. Returns {allowed, tokens_left, retry_after}.
_TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then tokens = capacity; ts = now end
local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * rate)
local allowed = 0
local retry = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
else
  retry = (requested - tokens) / rate
end
redis.call('HMSET', KEYS[1], 'tokens', tokens, 'ts', now)
local ttl = math.ceil(capacity / rate) + 2
redis.call('EXPIRE', KEYS[1], ttl)
return {allowed, tostring(tokens), tostring(retry)}
"""

# Atomic daily budget: allow and count only if under the limit (denied tries are not counted).
_DAILY_BUDGET_LUA = """
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local cur = tonumber(redis.call('GET', KEYS[1]) or '0')
if cur >= limit then return 0 end
local v = redis.call('INCR', KEYS[1])
if v == 1 then redis.call('EXPIRE', KEYS[1], ttl) end
return 1
"""


async def acquire_token(
    redis: aioredis.Redis,
    key: str,
    *,
    capacity: float,
    refill_rate: float,
    amount: float = 1.0,
    now: float | None = None,
) -> tuple[bool, float]:
    """Try to take ``amount`` tokens from the bucket ``key``.

    Returns ``(allowed, retry_after_seconds)``. ``now`` is injectable for tests.
    """
    ts = time.time() if now is None else now
    result = await redis.eval(_TOKEN_BUCKET_LUA, 1, key, capacity, refill_rate, ts, amount)
    allowed = bool(result[0])
    retry_after = float(result[2])
    return allowed, retry_after


async def check_daily_budget(
    redis: aioredis.Redis, key: str, *, limit: int, ttl_seconds: int
) -> bool:
    """Consume one unit of a daily budget; return False when the limit is reached."""
    result = await redis.eval(_DAILY_BUDGET_LUA, 1, key, limit, ttl_seconds)
    return bool(result)


def service_key(service: str) -> str:
    return f"rl:svc:{service}"


def tld_key(tld: str) -> str:
    return f"rl:whois:{tld.lower()}"


def daily_key(service: str, day: str) -> str:
    return f"budget:{service}:{day}"
