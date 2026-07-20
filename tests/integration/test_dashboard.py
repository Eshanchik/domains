"""Dashboard overview counts + vt_detect/health_down domain filters."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.db import SessionLocal
from app.models.alert import AlertEvent
from app.models.healthcheck import HealthCheck
from app.models.user import Role, User
from app.services import domains as domains_svc
from app.services.dashboard import build_overview
from app.services.domains import DomainFilter

NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


def _run(coro):
    return asyncio.run(coro)


def _admin() -> User:
    return User(email="a@e.com", login="a", password_hash="x", role=Role.admin, is_active=True)


def _add_vt_event(domain_id: int) -> None:
    async def _a():
        async with SessionLocal() as s:
            s.add(
                AlertEvent(
                    domain_id=domain_id,
                    kind="vt_malicious",
                    dedupe_key=f"{domain_id}:vt",
                    severity="high",
                    state="active",
                    fired_at=NOW,
                    payload_json={"malicious": 2},
                )
            )
            await s.commit()

    _run(_a())


def _add_down_healthcheck(domain_id: int) -> None:
    async def _a():
        async with SessionLocal() as s:
            s.add(
                HealthCheck(
                    domain_id=domain_id,
                    url="https://x/health",
                    state="down",
                    consecutive_failures=3,
                )
            )
            await s.commit()

    _run(_a())


def test_overview_counts(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    make_domain(proj, fqdn="soon7.com", expiry_date=NOW + timedelta(days=5))
    make_domain(proj, fqdn="soon30.com", expiry_date=NOW + timedelta(days=20))
    make_domain(proj, fqdn="soon90.com", expiry_date=NOW + timedelta(days=80))
    make_domain(proj, fqdn="far.com", expiry_date=NOW + timedelta(days=200))
    d_vt = make_domain(proj, fqdn="bad.com")
    d_hc = make_domain(proj, fqdn="down.com")
    _add_vt_event(d_vt)
    _add_down_healthcheck(d_hc)

    async def run():
        async with SessionLocal() as s:
            return await build_overview(s, _admin(), now=NOW)

    ov = _run(run())
    assert ov.total == 6
    assert ov.expiring_7 == 1  # soon7
    assert ov.expiring_30 == 2  # soon7 + soon30
    assert ov.expiring_90 == 3  # + soon90
    assert ov.vt_detects == 1
    assert ov.health_down == 1
    assert ov.by_company and ov.by_company[0].name == "ACME"
    assert ov.by_company[0].domains == 6


def test_vt_detect_filter(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    d_bad = make_domain(proj, fqdn="bad.com")
    make_domain(proj, fqdn="ok.com")
    _add_vt_event(d_bad)

    async def run():
        async with SessionLocal() as s:
            items, total = await domains_svc.list_domains(s, _admin(), DomainFilter(vt_detect=True))
            return [d.fqdn for d in items], total

    fqdns, total = _run(run())
    assert fqdns == ["bad.com"]
    assert total == 1


def test_health_down_filter(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    d_down = make_domain(proj, fqdn="down.com")
    make_domain(proj, fqdn="up.com")
    _add_down_healthcheck(d_down)

    async def run():
        async with SessionLocal() as s:
            items, total = await domains_svc.list_domains(
                s, _admin(), DomainFilter(health_down=True)
            )
            return [d.fqdn for d in items], total

    fqdns, total = _run(run())
    assert fqdns == ["down.com"]
    assert total == 1
