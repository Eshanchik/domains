"""Redis distributed locks for task idempotency (SPEC §CLAUDE — idempotency).

A lock is a SET NX with a random token and TTL. Release compares the token via Lua
so a lock is never released by another holder. Used to ensure a given check for a
given domain is not enqueued/run twice concurrently.
"""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
else
  return 0
end
"""


async def acquire_lock(redis: aioredis.Redis, key: str, *, ttl: int) -> str | None:
    """Acquire ``key`` for ``ttl`` seconds. Returns the token, or None if held."""
    token = secrets.token_hex(16)
    ok = await redis.set(key, token, nx=True, ex=ttl)
    return token if ok else None


async def release_lock(redis: aioredis.Redis, key: str, token: str) -> bool:
    """Release the lock only if we still hold it (token matches)."""
    result = await redis.eval(_RELEASE_LUA, 1, key, token)
    return bool(result)


@asynccontextmanager
async def redis_lock(redis: aioredis.Redis, key: str, *, ttl: int = 60):
    """Context manager yielding True if the lock was acquired, else False.

    Usage::

        async with redis_lock(redis, "lock:check:1:rdap", ttl=30) as got:
            if not got:
                return  # someone else is handling it
            ...
    """
    token = await acquire_lock(redis, key, ttl=ttl)
    try:
        yield token is not None
    finally:
        if token is not None:
            await release_lock(redis, key, token)
