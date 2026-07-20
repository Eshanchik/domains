"""Token bucket and daily budget rate limiters (real Redis)."""

from __future__ import annotations

import asyncio

from app.core.rate_limiter import acquire_token, check_daily_budget
from app.db import get_redis


async def _with_redis(coro_fn):
    redis = get_redis()
    try:
        return await coro_fn(redis)
    finally:
        await redis.aclose()


def test_token_bucket_allows_capacity_then_denies() -> None:
    async def run(redis):
        allowed = []
        for _ in range(6):
            ok, _retry = await acquire_token(
                redis, "rl:test:a", capacity=5, refill_rate=1, now=1000.0
            )
            allowed.append(ok)
        return allowed

    allowed = asyncio.run(_with_redis(run))
    assert allowed == [True, True, True, True, True, False]  # 5 tokens, 6th denied


def test_token_bucket_refills_over_time() -> None:
    async def run(redis):
        # Drain the bucket at t=0.
        for _ in range(5):
            await acquire_token(redis, "rl:test:b", capacity=5, refill_rate=1, now=0.0)
        denied, _ = await acquire_token(redis, "rl:test:b", capacity=5, refill_rate=1, now=0.0)
        # After 3 seconds at 1 token/s, 3 tokens are available again.
        after = [
            (await acquire_token(redis, "rl:test:b", capacity=5, refill_rate=1, now=3.0))[0]
            for _ in range(4)
        ]
        return denied, after

    denied, after = asyncio.run(_with_redis(run))
    assert denied is False
    assert after == [True, True, True, False]


def test_daily_budget_stops_at_limit() -> None:
    async def run(redis):
        results = []
        for _ in range(4):
            results.append(
                await check_daily_budget(redis, "budget:test:20260720", limit=3, ttl_seconds=60)
            )
        return results

    results = asyncio.run(_with_redis(run))
    assert results == [True, True, True, False]


def test_token_bucket_concurrent_grants_exactly_capacity() -> None:
    async def run(redis):
        async def one():
            ok, _ = await acquire_token(
                redis, "rl:test:conc", capacity=10, refill_rate=0.0001, now=500.0
            )
            return ok

        results = await asyncio.gather(*[one() for _ in range(50)])
        return sum(1 for r in results if r)

    granted = asyncio.run(_with_redis(run))
    assert granted == 10  # atomic: exactly capacity granted under concurrency
