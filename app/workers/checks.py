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
            elif check_type == "ssl":
                from app.checks.ssl_check import run_ssl_check

                status = await run_ssl_check(session, redis, domain_id)
                log.info("ssl check domain=%s → %s", domain_id, status)
            elif check_type == "vt":
                from app.checks.vt import run_vt_check

                status = await run_vt_check(session, redis, domain_id)
                log.info("vt check domain=%s → %s", domain_id, status)
            else:
                log.info("check type %s not implemented yet (domain=%s)", check_type, domain_id)
    finally:
        await redis.aclose()


@dramatiq.actor(max_retries=3, queue_name="checks")
def run_check(domain_id: int, check_type: str) -> None:
    """Entry point enqueued by the scheduler for a due (domain, check_type)."""
    asyncio.run(_run(check_type, domain_id))


async def _run_healthcheck(healthcheck_id: int) -> None:
    redis = get_redis()
    try:
        async with SessionLocal() as session:
            from app.checks.healthcheck import run_healthcheck as do_check

            outcome = await do_check(session, redis, healthcheck_id)
            log.info(
                "healthcheck %s → state=%s ok=%s transition=%s",
                healthcheck_id,
                outcome.state,
                outcome.ok,
                outcome.transition,
            )
    finally:
        await redis.aclose()


@dramatiq.actor(max_retries=3, queue_name="checks")
def run_healthcheck(healthcheck_id: int) -> None:
    """Entry point enqueued by the scheduler for a due health-check."""
    asyncio.run(_run_healthcheck(healthcheck_id))
