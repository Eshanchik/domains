"""Circuit breaker state machine (real Redis)."""

from __future__ import annotations

import asyncio

from app.core.circuit_breaker import CircuitBreaker
from app.db import get_redis


def _run(coro):
    return asyncio.run(coro)


async def _breaker(**kw) -> tuple[CircuitBreaker, object]:
    redis = get_redis()
    return CircuitBreaker(redis, "svc-test", **kw), redis


def test_opens_after_threshold_failures() -> None:
    async def run():
        cb, redis = await _breaker(threshold=3, cooldown=60)
        try:
            assert await cb.allow(now=100) is True
            for _ in range(3):
                await cb.record_failure(now=100)
            assert await cb.allow(now=100) is False  # open
            return True
        finally:
            await redis.aclose()

    assert _run(run())


def test_half_open_after_cooldown_and_success_closes() -> None:
    async def run():
        cb, redis = await _breaker(threshold=2, cooldown=30)
        try:
            for _ in range(2):
                await cb.record_failure(now=100)
            assert await cb.allow(now=100) is False  # open
            # After cooldown passes → half-open (trial allowed).
            assert await cb.allow(now=131) is True
            # A success closes it fully.
            await cb.record_success()
            assert await cb.allow(now=131) is True
            return True
        finally:
            await redis.aclose()

    assert _run(run())
