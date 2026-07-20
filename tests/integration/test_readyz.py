"""Readiness against live Postgres + Redis.

Runs for real in CI (services provided). Locally it skips if the datastores are
not reachable so ``make test`` does not hard-fail without a running stack.
"""

from __future__ import annotations

import asyncio

import pytest

from app.db import get_redis


def _datastores_reachable() -> bool:
    async def _probe() -> bool:
        # Use a throwaway NullPool engine so the probe never leaves a pooled
        # connection bound to this (soon-closed) event loop in the app's shared
        # engine — that would make the later /readyz call fail with a closed loop.
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        from app.config import settings

        probe_engine = create_async_engine(str(settings.database_url), poolclass=NullPool)
        redis_client = get_redis()
        try:
            async with probe_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await redis_client.ping()
            return True
        except Exception:
            return False
        finally:
            await redis_client.aclose()
            await probe_engine.dispose()

    return asyncio.run(_probe())


@pytest.mark.skipif(
    not _datastores_reachable(),
    reason="Postgres/Redis not reachable — integration test requires a running stack",
)
def test_readyz_ok(client) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok", "redis": "ok"}
