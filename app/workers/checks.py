"""Check actors — dispatch a due (domain, check_type) to its check implementation.

Actors are synchronous (Dramatiq); each wraps the async check logic in asyncio.run
with its own DB session and Redis client.
"""

from __future__ import annotations

import asyncio
import logging

import dramatiq

import app.workers.broker  # noqa: F401 — ensures the broker is configured on import
from app.db import SessionLocal, get_redis

log = logging.getLogger("worker.checks")


async def _run(check_type: str, domain_id: int) -> None:
    redis = get_redis()
    try:
        async with SessionLocal() as session:
            if check_type == "rdap":
                from app.checks.expiry import run_expiry_check

                status = await run_expiry_check(session, redis, domain_id)
                log.info("expiry check domain=%s → %s", domain_id, status)
            else:
                log.info("check type %s not implemented yet (domain=%s)", check_type, domain_id)
    finally:
        await redis.aclose()


@dramatiq.actor(max_retries=3, queue_name="checks")
def run_check(domain_id: int, check_type: str) -> None:
    """Entry point enqueued by the scheduler for a due (domain, check_type)."""
    asyncio.run(_run(check_type, domain_id))
