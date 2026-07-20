"""Scheduler selection, dispatch, advancement, and idempotency (DB + Redis)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal, get_redis
from app.models.check import CheckSchedule, CheckType
from app.scheduler.service import backfill_schedules, enqueue_due


def _run(coro):
    return asyncio.run(coro)


def test_backfill_creates_default_schedules(make_company, make_project, make_domain) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    make_domain(proj, fqdn="one.com")
    make_domain(proj, fqdn="two.com")

    async def run():
        async with SessionLocal() as s:
            created = await backfill_schedules(s, now=datetime.now(UTC))
            total = len((await s.execute(select(CheckSchedule))).scalars().all())
            # Second backfill is idempotent.
            created2 = await backfill_schedules(s, now=datetime.now(UTC))
            return created, total, created2

    created, total, created2 = _run(run())
    assert created == 6  # 2 domains × 3 default types
    assert total == 6
    assert created2 == 0


def test_enqueue_dispatches_due_and_advances(make_company, make_project, make_domain) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="due.com")

    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    sent: list[tuple[int, str]] = []

    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                s.add(
                    CheckSchedule(
                        domain_id=dom, type=CheckType.rdap, next_check_at=now - timedelta(hours=1)
                    )
                )
                s.add(
                    CheckSchedule(
                        domain_id=dom, type=CheckType.ssl, next_check_at=now + timedelta(days=1)
                    )
                )
                await s.commit()

            async with SessionLocal() as s:
                dispatched = await enqueue_due(
                    s, redis, now=now, send=lambda d, t: sent.append((d, t))
                )

            # Only the due (rdap) schedule fires; ssl is in the future.
            async with SessionLocal() as s:
                rdap = (
                    await s.execute(
                        select(CheckSchedule).where(
                            CheckSchedule.domain_id == dom, CheckSchedule.type == CheckType.rdap
                        )
                    )
                ).scalar_one()
                advanced = rdap.next_check_at
            return dispatched, advanced
        finally:
            await redis.aclose()

    dispatched, advanced = _run(run())
    assert dispatched == [(dom, "rdap")]
    assert sent == [(dom, "rdap")]
    assert advanced > now  # advanced into the future


def test_second_run_does_not_redispatch(make_company, make_project, make_domain) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="once.com")
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    async def run():
        redis = get_redis()
        sent: list = []
        try:
            async with SessionLocal() as s:
                s.add(
                    CheckSchedule(
                        domain_id=dom, type=CheckType.rdap, next_check_at=now - timedelta(hours=1)
                    )
                )
                await s.commit()
            async with SessionLocal() as s:
                await enqueue_due(s, redis, now=now, send=lambda d, t: sent.append((d, t)))
            # Immediately run again at the same instant: the row was advanced, so nothing due.
            async with SessionLocal() as s:
                again = await enqueue_due(s, redis, now=now, send=lambda d, t: sent.append((d, t)))
            return sent, again
        finally:
            await redis.aclose()

    sent, again = _run(run())
    assert len(sent) == 1
    assert again == []
