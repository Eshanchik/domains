"""Brute-force protection for login (AUTH-1 / SEC-5).

Counts consecutive failed attempts per login in Redis. Once the threshold is hit
the account is locked for a cool-down window; a successful login clears the counter.
Keyed by login identifier (case-folded); an IP dimension can be layered later.
"""

from __future__ import annotations

import redis.asyncio as aioredis

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60  # 15 minutes
_PREFIX = "login_fail:"


def _key(login: str) -> str:
    return f"{_PREFIX}{login.strip().lower()}"


async def is_locked(redis: aioredis.Redis, login: str) -> bool:
    """Return True if ``login`` currently exceeds the failed-attempt threshold."""
    value = await redis.get(_key(login))
    try:
        return value is not None and int(value) >= MAX_ATTEMPTS
    except (TypeError, ValueError):
        return False


async def record_failure(redis: aioredis.Redis, login: str) -> int:
    """Increment the failure counter and return the new count.

    The counter's TTL is (re)set to the lockout window on each failure, so a burst
    of failures keeps the account locked for the full cool-down after the last try.
    """
    key = _key(login)
    count = await redis.incr(key)
    await redis.expire(key, LOCKOUT_SECONDS)
    return int(count)


async def reset(redis: aioredis.Redis, login: str) -> None:
    """Clear the failure counter (called on a successful login)."""
    await redis.delete(_key(login))
