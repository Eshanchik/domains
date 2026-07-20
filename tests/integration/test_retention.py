"""History retention: drop old partitions, prune old health results."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import func, select, text

from app.checks.check_result_store import ensure_partition, write_result
from app.db import SessionLocal
from app.models.healthcheck import HealthCheck, HealthCheckResult
from app.services.retention import drop_old_partitions, prune_health_results

NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


def _run(coro):
    return asyncio.run(coro)


async def _partition_names(session) -> set[str]:
    rows = await session.execute(
        text(
            "SELECT inhrelid::regclass::text FROM pg_inherits "
            "WHERE inhparent = 'check_result'::regclass"
        )
    )
    return {name for (name,) in rows.all()}


def test_drop_old_partitions_keeps_recent(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")

    async def run():
        async with SessionLocal() as s:
            # Old (2024-01) and recent (2026-07) partitions, each with a row.
            await write_result(
                s,
                domain_id=dom,
                check_type="rdap",
                status="ok",
                checked_at=datetime(2024, 1, 15, tzinfo=UTC),
            )
            await write_result(
                s,
                domain_id=dom,
                check_type="rdap",
                status="ok",
                checked_at=NOW,
            )
            await s.commit()
            before = await _partition_names(s)
            dropped = await drop_old_partitions(s, now=NOW)
            after = await _partition_names(s)
            return before, dropped, after

    before, dropped, after = _run(run())
    assert "check_result_2024_01" in before
    assert "check_result_2024_01" in dropped
    assert "check_result_2024_01" not in after
    assert "check_result_2026_07" in after  # recent kept


def test_prune_old_health_results(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")

    async def run():
        async with SessionLocal() as s:
            hc = HealthCheck(domain_id=dom, url="https://x/health")
            s.add(hc)
            await s.flush()
            s.add(
                HealthCheckResult(
                    healthcheck_id=hc.id, ok=True, checked_at=datetime(2024, 1, 1, tzinfo=UTC)
                )
            )
            s.add(HealthCheckResult(healthcheck_id=hc.id, ok=True, checked_at=NOW))
            await s.commit()

            deleted = await prune_health_results(s, now=NOW)
            remaining = (
                await s.execute(select(func.count()).select_from(HealthCheckResult))
            ).scalar_one()
            return deleted, remaining

    deleted, remaining = _run(run())
    assert deleted == 1  # the 2024 row
    assert remaining == 1  # the recent row


def test_ensure_partition_idempotent():
    async def run():
        async with SessionLocal() as s:
            n1 = await ensure_partition(s, NOW)
            n2 = await ensure_partition(s, NOW)
            await s.commit()
            return n1, n2

    n1, n2 = _run(run())
    assert n1 == n2 == "check_result_2026_07"
