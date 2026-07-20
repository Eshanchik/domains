"""Redis-backed circuit breaker per external service (NFR-3).

After ``threshold`` consecutive failures the breaker opens for ``cooldown`` seconds
and calls short-circuit. When the cooldown passes the breaker goes half-open: the
next call is allowed as a trial — success closes it, failure re-opens it.
"""

from __future__ import annotations

import time

import redis.asyncio as aioredis


class CircuitBreaker:
    def __init__(
        self,
        redis: aioredis.Redis,
        service: str,
        *,
        threshold: int = 5,
        cooldown: float = 60.0,
        window: float = 120.0,
    ) -> None:
        self.redis = redis
        self.service = service
        self.threshold = threshold
        self.cooldown = cooldown
        self.window = window

    @property
    def _fails_key(self) -> str:
        return f"cb:{self.service}:fails"

    @property
    def _open_key(self) -> str:
        return f"cb:{self.service}:open_until"

    async def allow(self, *, now: float | None = None) -> bool:
        """Return True if a call may proceed (closed or half-open trial)."""
        ts = time.time() if now is None else now
        open_until = await self.redis.get(self._open_key)
        return not (open_until is not None and ts < float(open_until))

    async def record_success(self) -> None:
        await self.redis.delete(self._fails_key, self._open_key)

    async def record_failure(self, *, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        fails = await self.redis.incr(self._fails_key)
        await self.redis.expire(self._fails_key, int(self.window))
        if fails >= self.threshold:
            await self.redis.set(self._open_key, ts + self.cooldown, ex=int(self.cooldown) + 1)

    async def is_open(self, *, now: float | None = None) -> bool:
        return not await self.allow(now=now)
