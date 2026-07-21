"""Registrar sync: merge/stage, manual-safe, auth error, assign (DB)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.connectors.base import ConnectorError, RegistrarConnector, RegistrarDomain
from app.db import SessionLocal
from app.models.domain import Domain
from app.models.registrar import RegistrarAccount, UnassignedDomain
from app.services import registrars as svc


def _run(coro):
    return asyncio.run(coro)


class FakeConnector(RegistrarConnector):
    def __init__(self, domains=None, error=None):
        self._domains = domains or []
        self._error = error

    async def list_domains(self):
        if self._error:
            raise ConnectorError(self._error)
        return self._domains


def _make_account() -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            acc = await svc.create_namecheap_account(
                s,
                label="main",
                api_user="u",
                api_key="SECRETKEY",
                username="u",
                client_ip="1.2.3.4",
                actor_id=None,
            )
            return acc.id

    return _run(_c())


def test_credentials_encrypted():
    aid = _make_account()

    async def raw():
        async with SessionLocal() as s:
            return (await s.get(RegistrarAccount, aid)).credentials_enc

    enc = _run(raw())
    assert "SECRETKEY" not in enc  # api key stored encrypted


def test_sync_merges_existing_and_stages_new(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    make_domain(proj, fqdn="existing.com")  # already tracked
    aid = _make_account()

    conn = FakeConnector(
        domains=[
            RegistrarDomain("existing.com", datetime(2027, 5, 1, tzinfo=UTC), True),
            RegistrarDomain("newone.com", datetime(2027, 6, 1, tzinfo=UTC), False),
        ]
    )

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            report = await svc.sync_account(s, acc, connector=conn)
        async with SessionLocal() as s:
            existing = (
                await s.execute(select(Domain).where(Domain.fqdn == "existing.com"))
            ).scalar_one()
            staged = (
                await s.execute(select(func.count()).select_from(UnassignedDomain))
            ).scalar_one()
            return (
                report,
                existing.expiry_date,
                existing.auto_renew,
                existing.registrar_account_id,
                staged,
            )

    report, expiry, auto_renew, acct_link, staged = _run(run())
    assert report.updated == 1 and report.staged == 1
    assert expiry.year == 2027
    assert auto_renew is True
    assert acct_link == aid
    assert staged == 1  # newone.com staged


def test_sync_does_not_overwrite_manual(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    manual_expiry = datetime(2030, 1, 1, tzinfo=UTC)
    make_domain(
        proj,
        fqdn="manual.com",
        expiry_date=manual_expiry,
        field_sources={"fqdn": "manual", "expiry_date": "manual"},
    )
    aid = _make_account()
    conn = FakeConnector(
        domains=[RegistrarDomain("manual.com", datetime(2027, 1, 1, tzinfo=UTC), True)]
    )

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            await svc.sync_account(s, acc, connector=conn)
        async with SessionLocal() as s:
            return (
                await s.execute(select(Domain.expiry_date).where(Domain.fqdn == "manual.com"))
            ).scalar_one()

    assert _run(run()) == manual_expiry  # manual expiry untouched


def test_sync_creates_in_default_project(make_company, make_project):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")

    async def _c() -> int:
        async with SessionLocal() as s:
            acc = await svc.create_namecheap_account(
                s,
                label="main",
                api_user="u",
                api_key="SECRETKEY",
                username="u",
                client_ip="1.2.3.4",
                actor_id=None,
                default_project_id=proj,
            )
            return acc.id

    aid = _run(_c())
    conn = FakeConnector(
        domains=[RegistrarDomain("auto1.com", datetime(2027, 1, 1, tzinfo=UTC), True)]
    )

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            report = await svc.sync_account(s, acc, connector=conn)
        async with SessionLocal() as s:
            d = (
                await s.execute(select(Domain).where(Domain.fqdn == "auto1.com"))
            ).scalar_one_or_none()
            staged = (
                await s.execute(select(func.count()).select_from(UnassignedDomain))
            ).scalar_one()
            return report, d, staged

    report, domain, staged = _run(run())
    # Domain went straight into the default project, not the unassigned queue.
    assert report.created == 1 and report.staged == 0
    assert domain is not None
    assert domain.project_id == proj
    assert domain.registrar_account_id == aid
    assert (domain.field_sources or {}).get("project_id") == "manual"
    assert staged == 0


def test_sync_auth_error_marks_account(make_company, make_project):
    aid = _make_account()
    conn = FakeConnector(error="API Key is invalid")

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            report = await svc.sync_account(s, acc, connector=conn)
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            return report.error, acc.status, acc.last_error

    err, status, last_error = _run(run())
    assert err and "invalid" in err
    assert status == "error"
    assert "invalid" in last_error


def test_assign_unassigned_creates_domain(make_company, make_project):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    aid = _make_account()

    async def setup() -> int:
        async with SessionLocal() as s:
            u = UnassignedDomain(
                registrar_account_id=aid,
                fqdn="fresh.com",
                expiry_date=datetime(2027, 9, 1, tzinfo=UTC),
                auto_renew=True,
            )
            s.add(u)
            await s.commit()
            await s.refresh(u)
            return u.id

    uid = _run(setup())

    async def run():
        async with SessionLocal() as s:
            await svc.assign_to_project(s, uid, proj, actor_id=None)
        async with SessionLocal() as s:
            dom = (
                await s.execute(select(Domain).where(Domain.fqdn == "fresh.com"))
            ).scalar_one_or_none()
            remaining = (
                await s.execute(select(func.count()).select_from(UnassignedDomain))
            ).scalar_one()
            return dom, remaining

    dom, remaining = _run(run())
    assert dom is not None and dom.project_id == proj
    assert remaining == 0  # staging row removed


def test_godaddy_account_dispatch(make_company, make_project):
    """Creating a GoDaddy account wires build_account_connector to GoDaddyConnector."""

    async def run():
        from app.connectors.godaddy import GoDaddyConnector

        async with SessionLocal() as s:
            acc = await svc.create_godaddy_account(
                s, label="gd", api_key="KEY", api_secret="SECRET", actor_id=None
            )
            conn = await svc.build_account_connector(s, acc)
            return acc.credentials_enc, isinstance(conn, GoDaddyConnector)

    enc, is_godaddy = _run(run())
    assert "SECRET" not in enc  # creds encrypted
    assert is_godaddy is True
