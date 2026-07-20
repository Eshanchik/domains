"""Alert engine: threshold events, dedup (no spam), threshold crossing, resolve."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.alert import AlertEvent
from app.models.domain import Domain
from app.services import alerts


def _run(coro):
    return asyncio.run(coro)


NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


async def _domain(session, dom_id) -> Domain:
    return await session.get(Domain, dom_id)


def _active_count() -> int:
    async def _c():
        async with SessionLocal() as s:
            return await _active_count_in(s)

    return _run(_c())


async def _active_count_in(session) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(AlertEvent).where(AlertEvent.state == "active")
        )
    ).scalar_one()


def test_expiry_event_created_once_no_spam(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com", expiry_date=NOW + timedelta(days=20))

    async def run():
        created_counts = []
        async with SessionLocal() as s:
            d = await _domain(s, dom)
            for _ in range(3):  # three evaluations at the same band (20 days ≤ 30)
                events = await alerts.evaluate_expiry(s, d, now=NOW)
                await s.commit()
                created_counts.append(len(events))
        return created_counts

    counts = _run(run())
    assert counts == [1, 0, 0]  # fires once, then dedup
    assert _active_count() == 1


def test_expiry_threshold_crossing_fires_new_event(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com", expiry_date=NOW + timedelta(days=20))

    async def run():
        async with SessionLocal() as s:
            d = await _domain(s, dom)
            first = await alerts.evaluate_expiry(s, d, now=NOW)  # band 30
            await s.commit()
            # Domain now 10 days out → crosses the 14-day band.
            d.expiry_date = NOW + timedelta(days=10)
            second = await alerts.evaluate_expiry(s, d, now=NOW)
            await s.commit()
            severities = [e.severity for e in first + second]
            return len(first), len(second), severities

    n1, n2, sevs = _run(run())
    assert n1 == 1  # 30-day band
    assert n2 == 1  # new 14-day band event
    # Only the tightest (14) band stays active; the 30 band was resolved.
    assert _active_count() == 1


def test_expiry_high_severity_within_7_days(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com", expiry_date=NOW + timedelta(days=5))

    async def run():
        async with SessionLocal() as s:
            d = await _domain(s, dom)
            events = await alerts.evaluate_expiry(s, d, now=NOW)
            await s.commit()
            return events[0].severity

    assert _run(run()) == "high"


def test_expiry_resolves_when_renewed(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com", expiry_date=NOW + timedelta(days=10))

    async def run():
        async with SessionLocal() as s:
            d = await _domain(s, dom)
            await alerts.evaluate_expiry(s, d, now=NOW)
            await s.commit()
            active_before = await _active_count_in(s)
            # Renew far into the future.
            d.expiry_date = NOW + timedelta(days=300)
            await alerts.evaluate_expiry(s, d, now=NOW)
            await s.commit()
            return active_before

    active_before = _run(run())
    assert active_before == 1
    assert _active_count() == 0  # resolved after renewal


def test_vt_malicious_high_then_resolves(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="bad.com")

    async def run():
        async with SessionLocal() as s:
            d = await _domain(s, dom)
            first = await alerts.evaluate_vt(s, d, 3, now=NOW)
            await s.commit()
            dup = await alerts.evaluate_vt(s, d, 3, now=NOW)  # still malicious → no new event
            await s.commit()
            await alerts.evaluate_vt(s, d, 0, now=NOW)  # resolves
            await s.commit()
            return first[0].severity, len(dup)

    severity, dup = _run(run())
    assert severity == "high"
    assert dup == 0
    assert _active_count() == 0


def test_health_down_then_recovered(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")

    async def run():
        async with SessionLocal() as s:
            down = await alerts.evaluate_health(s, dom, 99, "down", now=NOW)
            await s.commit()
            active_after_down = await _active_count_in(s)
            await alerts.evaluate_health(s, dom, 99, "recovered", now=NOW)
            await s.commit()
            return down[0].severity, active_after_down

    severity, active_after_down = _run(run())
    assert severity == "high"
    assert active_after_down == 1
    assert _active_count() == 0


def test_dispatch_instant_sends_high_severity(make_company, make_project, make_domain):
    from app.services import notifications as notif

    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com", expiry_date=NOW + timedelta(days=3))

    sent: list[tuple[int, str, int]] = []

    async def run():
        async with SessionLocal() as s:
            # A global channel so resolution finds a target.
            await notif.create_channel(
                s,
                name="glob",
                chat_id="-100",
                company_id=None,
                project_id=None,
                is_default=True,
                mode="both",
                digest_time=None,
                actor_id=None,
            )
            d = await _domain(s, dom)
            events = await alerts.evaluate_expiry(s, d, now=NOW)  # high (≤7)
            await s.commit()
            count = await alerts.dispatch_instant(
                s, None, d, events, send=lambda cid, text, eid: sent.append((cid, text, eid))
            )
            return count

    count = _run(run())
    assert count == 1
    assert sent and "истекает" in sent[0][1]
