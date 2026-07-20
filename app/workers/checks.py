"""Check actors — dispatch a due (domain, check_type) to its check implementation.

Actors are synchronous (Dramatiq); each wraps the async check logic in ``asyncio.run``
with its own DB session and Redis client.

Each worker thread runs its own event loop per ``asyncio.run``. Async SQLAlchemy
engines are NOT safe to share a connection pool across event loops, so every actor
invocation gets a fresh ``NullPool`` engine (``worker_session``) disposed on exit —
otherwise pooled asyncpg connections bound to another thread's (closed) loop raise
"attached to a different loop" / "Event loop is closed".
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import dramatiq
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.workers.broker  # noqa: F401 — ensures the broker is configured on import
from app.config import settings
from app.db import get_redis

log = logging.getLogger("worker.checks")


@asynccontextmanager
async def worker_session():
    """Yield a session from a per-invocation NullPool engine, disposed on exit."""
    engine = create_async_engine(str(settings.database_url), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            yield session
    finally:
        await engine.dispose()


async def _run(check_type: str, domain_id: int) -> None:
    redis = get_redis()
    try:
        async with worker_session() as session:
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
            elif check_type == "dns":
                from app.checks.dns_check import run_dns_check

                status = await run_dns_check(session, redis, domain_id)
                log.info("dns check domain=%s → %s", domain_id, status)
            else:
                log.info("check type %s not implemented yet (domain=%s)", check_type, domain_id)
                return

            # Evaluate alert rules for this check and dispatch instant alerts.
            from app.services.alerts import evaluate_after_check

            await evaluate_after_check(session, redis, domain_id, check_type)
    finally:
        await redis.aclose()


@dramatiq.actor(max_retries=3, queue_name="checks")
def run_check(domain_id: int, check_type: str) -> None:
    """Entry point enqueued by the scheduler for a due (domain, check_type)."""
    asyncio.run(_run(check_type, domain_id))


async def _run_healthcheck(healthcheck_id: int) -> None:
    redis = get_redis()
    try:
        async with worker_session() as session:
            from app.checks.healthcheck import run_healthcheck as do_check

            outcome = await do_check(session, redis, healthcheck_id)
            log.info(
                "healthcheck %s → state=%s ok=%s transition=%s",
                healthcheck_id,
                outcome.state,
                outcome.ok,
                outcome.transition,
            )
            if outcome.transition:
                from app.models.healthcheck import HealthCheck
                from app.services.alerts import evaluate_after_healthcheck

                hc = await session.get(HealthCheck, healthcheck_id)
                if hc is not None:
                    await evaluate_after_healthcheck(
                        session, redis, hc.domain_id, healthcheck_id, outcome.transition
                    )
    finally:
        await redis.aclose()


@dramatiq.actor(max_retries=3, queue_name="checks")
def run_healthcheck(healthcheck_id: int) -> None:
    """Entry point enqueued by the scheduler for a due health-check."""
    asyncio.run(_run_healthcheck(healthcheck_id))


async def _send_notification(channel_id: int, text: str, alert_event_id: int | None) -> None:
    redis = get_redis()
    try:
        async with worker_session() as session:
            from app.services import notifications as notif

            channel = await notif.get_channel(session, channel_id)
            if channel is not None:
                await notif.send_to_channel(
                    session, redis, channel, text, alert_event_id=alert_event_id
                )
    finally:
        await redis.aclose()


@dramatiq.actor(max_retries=3, queue_name="notifications")
def send_notification(channel_id: int, text: str, alert_event_id: int | None = None) -> None:
    """Deliver a message to a channel (used by the alerter/digest)."""
    asyncio.run(_send_notification(channel_id, text, alert_event_id))


async def _sync_registrar_account(account_id: int) -> None:
    async with worker_session() as session:
        from app.services import registrars as reg

        account = await reg.get_account(session, account_id)
        if account is None:
            return
        report = await reg.sync_account(session, account)
        log.info(
            "registrar sync account=%s updated=%s staged=%s error=%s",
            account_id,
            report.updated,
            report.staged,
            report.error,
        )


@dramatiq.actor(max_retries=2, queue_name="sync")
def sync_registrar_account(account_id: int) -> None:
    """Pull domains from a registrar account (manual or periodic)."""
    asyncio.run(_sync_registrar_account(account_id))
