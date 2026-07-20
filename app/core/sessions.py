"""Server-side session store backed by Redis (AUTH-1).

A session is a random high-entropy token stored in the cookie; Redis maps the
token to a user id with a sliding TTL. Server-side storage means logout and
forced-invalidation actually revoke access (unlike stateless signed cookies).
"""

from __future__ import annotations

import secrets

import redis.asyncio as aioredis

SESSION_COOKIE = "dg_session"
SESSION_TTL_SECONDS = 14 * 24 * 3600  # 14 days, sliding
_PREFIX = "session:"


def _key(token: str) -> str:
    return f"{_PREFIX}{token}"


async def create_session(redis: aioredis.Redis, user_id: int) -> str:
    """Create a session for ``user_id`` and return its token."""
    token = secrets.token_urlsafe(32)
    await redis.set(_key(token), str(user_id), ex=SESSION_TTL_SECONDS)
    return token


async def get_session_user(redis: aioredis.Redis, token: str) -> int | None:
    """Return the user id for ``token`` (refreshing its TTL), or None if unknown."""
    if not token:
        return None
    value = await redis.get(_key(token))
    if value is None:
        return None
    # Sliding expiration: extend on use.
    await redis.expire(_key(token), SESSION_TTL_SECONDS)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def destroy_session(redis: aioredis.Redis, token: str) -> None:
    """Delete a session token (logout)."""
    if token:
        await redis.delete(_key(token))
