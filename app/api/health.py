"""Liveness and readiness endpoints (NFR-5).

- ``/healthz``: process is alive (no external dependencies touched).
- ``/readyz``: process can serve traffic — verifies Postgres and Redis are reachable.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_redis, get_session

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe — always OK if the event loop is running."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Readiness probe — reports per-dependency health.

    Returns HTTP 503 if any critical dependency is unreachable so orchestrators
    hold traffic until the service is truly ready.
    """
    checks: dict[str, str] = {}

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — surface any driver/connectivity error
        checks["database"] = f"error: {exc.__class__.__name__}"

    redis_client: aioredis.Redis = get_redis()
    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc.__class__.__name__}"
    finally:
        await redis_client.aclose()

    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": checks}
