"""Scheduler core: enqueue mature checks with jitter and idempotency locks.

Selection is driven by ``CheckSchedule (type, next_check_at)``. Each due row is
locked (so overlapping scheduler runs don't double-enqueue), dispatched to the
worker, and its ``next_check_at`` advanced by the per-type interval plus jitter.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.locks import redis_lock
from app.models.check import CheckSchedule, CheckType
from app.models.domain import Domain
from app.models.healthcheck import HealthCheck

# Base interval per check type (SPEC §3.5).
INTERVALS: dict[CheckType, timedelta] = {
    CheckType.rdap: timedelta(days=1),
    CheckType.whois: timedelta(days=1),
    CheckType.ssl: timedelta(days=1),
    CheckType.vt: timedelta(days=7),
    CheckType.healthcheck: timedelta(minutes=15),
}

# Types every domain gets by default (health-checks are per-URL, added in T10).
DEFAULT_TYPES = (CheckType.rdap, CheckType.ssl, CheckType.vt)


def _default_send(domain_id: int, check_type: str) -> None:
    from app.workers.checks import run_check

    run_check.send(domain_id, check_type)


def _default_send_healthcheck(healthcheck_id: int) -> None:
    from app.workers.checks import run_healthcheck

    run_healthcheck.send(healthcheck_id)


def _default_send_sync(account_id: int) -> None:
    from app.workers.checks import sync_registrar_account

    sync_registrar_account.send(account_id)


async def enqueue_due_registrar_syncs(
    session: AsyncSession,
    redis: aioredis.Redis,
    *,
    now: datetime | None = None,
    interval_hours: int = 6,
    send: Callable[[int], None] = _default_send_sync,
) -> list[int]:
    """Enqueue sync for enabled accounts not synced within ``interval_hours``."""
    from app.models.registrar import RegistrarAccount

    ts = now or datetime.now(UTC)
    cutoff = ts - timedelta(hours=interval_hours)
    result = await session.execute(
        select(RegistrarAccount).where(
            RegistrarAccount.is_enabled.is_(True),
            or_(
                RegistrarAccount.last_sync_at.is_(None),
                RegistrarAccount.last_sync_at <= cutoff,
            ),
        )
    )
    dispatched: list[int] = []
    for account in result.scalars().all():
        lock_key = f"lock:regsync:{account.id}"
        async with redis_lock(redis, lock_key, ttl=3600) as got:
            if not got:
                continue
            send(account.id)
            # Provisional bump so we don't re-enqueue next tick; the sync sets the real value.
            account.last_sync_at = ts
            dispatched.append(account.id)
    await session.commit()
    return dispatched


def _next_at(now: datetime, ctype: CheckType, jitter_frac: float) -> datetime:
    interval = INTERVALS[ctype]
    jitter = interval * random.uniform(0, jitter_frac)
    return now + interval + jitter


async def enqueue_due(
    session: AsyncSession,
    redis: aioredis.Redis,
    *,
    now: datetime | None = None,
    batch_size: int = 200,
    lock_ttl: int = 300,
    jitter_frac: float = 0.1,
    send: Callable[[int, str], None] = _default_send,
) -> list[tuple[int, str]]:
    """Dispatch all schedules due at ``now`` and advance them. Returns what was sent."""
    ts = now or datetime.now(UTC)
    result = await session.execute(
        select(CheckSchedule)
        .where(CheckSchedule.next_check_at <= ts)
        .order_by(CheckSchedule.next_check_at)
        .limit(batch_size)
    )
    due = list(result.scalars().all())

    dispatched: list[tuple[int, str]] = []
    for sched in due:
        lock_key = f"lock:check:{sched.domain_id}:{sched.type.value}"
        async with redis_lock(redis, lock_key, ttl=lock_ttl) as got:
            if not got:
                continue
            send(sched.domain_id, sched.type.value)
            sched.next_check_at = _next_at(ts, sched.type, jitter_frac)
            dispatched.append((sched.domain_id, sched.type.value))
    await session.commit()
    return dispatched


async def enqueue_due_healthchecks(
    session: AsyncSession,
    redis: aioredis.Redis,
    *,
    now: datetime | None = None,
    batch_size: int = 500,
    lock_ttl: int = 120,
    send: Callable[[int], None] = _default_send_healthcheck,
) -> list[int]:
    """Dispatch enabled health-checks whose next_check_at is due. Returns their ids.

    Each health-check advances its own ``next_check_at`` inside ``run_healthcheck``;
    here we bump it forward provisionally so an immediate re-poll won't re-enqueue.
    """
    ts = now or datetime.now(UTC)
    result = await session.execute(
        select(HealthCheck)
        .where(HealthCheck.is_enabled.is_(True), HealthCheck.next_check_at <= ts)
        .order_by(HealthCheck.next_check_at)
        .limit(batch_size)
    )
    due = list(result.scalars().all())

    dispatched: list[int] = []
    for hc in due:
        lock_key = f"lock:healthcheck:{hc.id}"
        async with redis_lock(redis, lock_key, ttl=lock_ttl) as got:
            if not got:
                continue
            send(hc.id)
            # Provisional bump; the worker sets the authoritative next_check_at.
            hc.next_check_at = ts + timedelta(minutes=hc.interval_min)
            dispatched.append(hc.id)
    await session.commit()
    return dispatched


async def backfill_schedules(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    types: tuple[CheckType, ...] = DEFAULT_TYPES,
) -> int:
    """Create missing CheckSchedule rows (due immediately) for active domains."""
    ts = now or datetime.now(UTC)
    domain_ids = list(
        (await session.execute(select(Domain.id).where(Domain.is_active.is_(True)))).scalars().all()
    )
    existing = {
        (d, t)
        for d, t in (
            await session.execute(select(CheckSchedule.domain_id, CheckSchedule.type))
        ).all()
    }
    created = 0
    for domain_id in domain_ids:
        for ctype in types:
            if (domain_id, ctype) not in existing:
                session.add(CheckSchedule(domain_id=domain_id, type=ctype, next_check_at=ts))
                created += 1
    await session.commit()
    return created
