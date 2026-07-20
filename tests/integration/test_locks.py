"""Redis distributed locks (real Redis)."""

from __future__ import annotations

import asyncio

from app.core.locks import acquire_lock, redis_lock, release_lock
from app.db import get_redis


def _run(coro):
    return asyncio.run(coro)


def test_second_acquire_blocked_until_release() -> None:
    async def run():
        redis = get_redis()
        try:
            t1 = await acquire_lock(redis, "lock:x", ttl=30)
            t2 = await acquire_lock(redis, "lock:x", ttl=30)
            assert t1 is not None
            assert t2 is None  # already held
            assert await release_lock(redis, "lock:x", t1) is True
            t3 = await acquire_lock(redis, "lock:x", ttl=30)
            assert t3 is not None  # free again
            await release_lock(redis, "lock:x", t3)
        finally:
            await redis.aclose()

    _run(run())


def test_release_only_by_holder() -> None:
    async def run():
        redis = get_redis()
        try:
            token = await acquire_lock(redis, "lock:y", ttl=30)
            assert await release_lock(redis, "lock:y", "not-the-token") is False
            assert await release_lock(redis, "lock:y", token) is True
        finally:
            await redis.aclose()

    _run(run())


def test_context_manager_reports_contention() -> None:
    async def run():
        redis = get_redis()
        try:
            async with redis_lock(redis, "lock:z", ttl=30) as got_first:
                assert got_first is True
                async with redis_lock(redis, "lock:z", ttl=30) as got_second:
                    assert got_second is False  # held by outer
            # Released after the outer block.
            async with redis_lock(redis, "lock:z", ttl=30) as got_third:
                assert got_third is True
        finally:
            await redis.aclose()

    _run(run())
