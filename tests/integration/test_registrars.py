"""Registrar sync: merge/stage, manual-safe, auth error, assign (DB)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.connectors.base import ConnectorError, RegistrarConnector, RegistrarDomain
from app.core import crypto
from app.db import SessionLocal
from app.models.domain import Domain
from app.models.registrar import RegistrarAccount, UnassignedDomain
from app.models.user import Role
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


def test_godaddy_sync_tags_source(make_company, make_project):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")

    async def _c() -> int:
        async with SessionLocal() as s:
            acc = await svc.create_godaddy_account(
                s, label="gd", api_key="k", api_secret="s", actor_id=None, default_project_id=proj
            )
            return acc.id

    aid = _run(_c())
    conn = FakeConnector(domains=[RegistrarDomain("gdauto.com", datetime(2027, 1, 1, tzinfo=UTC))])

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            await svc.sync_account(s, acc, connector=conn)
        async with SessionLocal() as s:
            return (await s.execute(select(Domain).where(Domain.fqdn == "gdauto.com"))).scalar_one()

    d = _run(run())
    # Source label reflects the actual connector, not the hardcoded namecheap one.
    assert (d.field_sources or {}).get("fqdn") == "api-godaddy"


def test_archive_expired_scoped(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")

    async def _c() -> int:
        async with SessionLocal() as s:
            acc = await svc.create_godaddy_account(
                s, label="gd", api_key="k", api_secret="s", actor_id=None
            )
            return acc.id

    aid = _run(_c())
    make_domain(
        proj,
        fqdn="dead.com",
        registrar_account_id=aid,
        expiry_date=datetime(2019, 1, 1, tzinfo=UTC),
    )
    make_domain(
        proj,
        fqdn="alive.com",
        registrar_account_id=aid,
        expiry_date=datetime(2030, 1, 1, tzinfo=UTC),
    )

    async def run():
        async with SessionLocal() as s:
            preview = await svc.archive_expired(s, connector_type="godaddy", apply=False)
        async with SessionLocal() as s:
            applied = await svc.archive_expired(s, connector_type="godaddy", apply=True)
        async with SessionLocal() as s:
            active = {
                d.fqdn: d.is_active
                for d in (
                    await s.execute(
                        select(Domain).where(Domain.fqdn.in_(["dead.com", "alive.com"]))
                    )
                )
                .scalars()
                .all()
            }
        return preview, applied, active

    preview, applied, active = _run(run())
    assert preview == ["dead.com"] and applied == ["dead.com"]  # only the past-expiry one
    assert active["dead.com"] is False  # archived
    assert active["alive.com"] is True  # future expiry untouched


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


# --- T60: edit registrar account -------------------------------------------


def _creds(account_id: int) -> dict:
    async def _q():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, account_id)
            return json.loads(crypto.decrypt(acc.credentials_enc))

    return _run(_q())


def test_account_connector_type():
    aid = _make_account()

    async def run():
        async with SessionLocal() as s:
            return await svc.account_connector_type(s, await s.get(RegistrarAccount, aid))

    assert _run(run()) == "namecheap"


def test_update_account_changes_ip_keeps_blank_secret():
    aid = _make_account()  # client_ip 1.2.3.4, api_key SECRETKEY

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            await svc.update_account(
                s,
                acc,
                label="renamed",
                default_project_id=None,
                creds_updates={
                    "client_ip": "9.9.9.9",
                    "api_user": "",
                    "username": "",
                    "api_key": "",
                },
                actor_id=None,
            )
        async with SessionLocal() as s:
            return (await s.get(RegistrarAccount, aid)).label

    label = _run(run())
    creds = _creds(aid)
    assert label == "renamed"
    assert creds["client_ip"] == "9.9.9.9"  # IP updated
    assert creds["api_key"] == "SECRETKEY"  # blank field kept the stored secret


def test_update_account_rotates_secret_when_provided():
    aid = _make_account()

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            await svc.update_account(
                s,
                acc,
                label="main",
                default_project_id=None,
                creds_updates={"client_ip": "1.2.3.4", "api_key": "NEWKEY"},
                actor_id=None,
            )

    _run(run())
    assert _creds(aid)["api_key"] == "NEWKEY"


def test_edit_form_and_update_via_web(client, make_user):
    make_user(login="root", password="password123", role=Role.admin)
    client.post("/login", data={"login": "root", "password": "password123"})
    aid = _make_account()

    form = client.get(f"/registrars/{aid}/edit")
    assert form.status_code == 200
    assert "1.2.3.4" in form.text  # current IP pre-filled

    resp = client.post(
        f"/registrars/{aid}",
        data={
            "label": "main",
            "default_project_id": "",
            "client_ip": "5.6.7.8",
            "api_user": "",
            "username": "",
            "api_key": "",  # blank → keep stored secret
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    creds = _creds(aid)
    assert creds["client_ip"] == "5.6.7.8"
    assert creds["api_key"] == "SECRETKEY"


def test_edit_requires_admin(client, make_user):
    make_user(login="v", password="password123", role=Role.viewer)
    client.post("/login", data={"login": "v", "password": "password123"})
    aid = _make_account()
    assert client.get(f"/registrars/{aid}/edit", follow_redirects=False).status_code == 403


def _login_admin(client, make_user):
    make_user(login="root", password="password123", role=Role.admin)
    client.post("/login", data={"login": "root", "password": "password123"})


def _make_godaddy() -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            acc = await svc.create_godaddy_account(
                s, label="gd", api_key="GKEY", api_secret="GSECRET", actor_id=None
            )
            return acc.id

    return _run(_c())


def test_update_aborts_on_undecryptable_credentials():
    """A present-but-undecryptable blob must NOT be overwritten (irreversible loss)."""
    aid = _make_account()

    async def corrupt():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            acc.credentials_enc = "not-a-valid-fernet-token"
            await s.commit()

    _run(corrupt())

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            try:
                await svc.update_account(
                    s, acc, label="x", default_project_id=None,
                    creds_updates={"client_ip": "9.9.9.9"}, actor_id=None,
                )
                return "updated"
            except svc.CredentialDecryptError:
                return "aborted"

    assert _run(run()) == "aborted"

    async def blob():
        async with SessionLocal() as s:
            return (await s.get(RegistrarAccount, aid)).credentials_enc

    assert _run(blob()) == "not-a-valid-fernet-token"  # original blob preserved


def test_update_strips_whitespace():
    aid = _make_account()

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            await svc.update_account(
                s, acc, label="m", default_project_id=None,
                creds_updates={"api_key": "  NEWKEY \n", "client_ip": "1.2.3.4"}, actor_id=None,
            )

    _run(run())
    assert _creds(aid)["api_key"] == "NEWKEY"


def test_update_clears_stale_error():
    aid = _make_account()

    async def seed_error():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            acc.status = "error"
            acc.last_error = "boom"
            await s.commit()

    _run(seed_error())

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            await svc.update_account(
                s, acc, label="m", default_project_id=None,
                creds_updates={"client_ip": "1.2.3.4"}, actor_id=None,
            )
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            return acc.status, acc.last_error

    st, le = _run(run())
    assert st == "ok" and le is None


def test_edit_invalid_ip_rejected(client, make_user):
    _login_admin(client, make_user)
    aid = _make_account()  # client_ip 1.2.3.4
    resp = client.post(
        f"/registrars/{aid}",
        data={
            "label": "m",
            "default_project_id": "",
            "client_ip": "nope",
            "api_user": "",
            "username": "",
            "api_key": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert _creds(aid)["client_ip"] == "1.2.3.4"  # unchanged


def test_edit_godaddy_keeps_blank_secret(client, make_user):
    _login_admin(client, make_user)
    aid = _make_godaddy()

    form = client.get(f"/registrars/{aid}/edit")
    assert form.status_code == 200
    assert "api_secret" in form.text
    assert 'name="client_ip"' not in form.text  # GoDaddy has no Client IP field

    resp = client.post(
        f"/registrars/{aid}",
        data={"label": "gd", "default_project_id": "", "api_key": "NEWGKEY", "api_secret": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    creds = _creds(aid)
    assert creds["api_key"] == "NEWGKEY"  # rotated
    assert creds["api_secret"] == "GSECRET"  # blank kept the stored secret


def test_edit_change_default_project(client, make_user, make_company, make_project):
    comp = make_company(code="acme")
    proj = make_project(comp, code="web")
    _login_admin(client, make_user)

    async def mk() -> int:
        async with SessionLocal() as s:
            acc = await svc.create_namecheap_account(
                s, label="m", api_user="u", api_key="K", username="u", client_ip="1.2.3.4",
                actor_id=None, default_project_id=proj,
            )
            return acc.id

    aid = _run(mk())
    client.post(
        f"/registrars/{aid}",
        data={"label": "m", "default_project_id": "", "client_ip": "1.2.3.4"},
        follow_redirects=False,
    )

    async def get_dp():
        async with SessionLocal() as s:
            return (await s.get(RegistrarAccount, aid)).default_project_id

    assert _run(get_dp()) is None  # moved back to unassigned


def test_edit_missing_account_redirects(client, make_user):
    _login_admin(client, make_user)
    assert client.get("/registrars/999999/edit", follow_redirects=False).status_code == 303
    assert (
        client.post("/registrars/999999", data={"label": "x"}, follow_redirects=False).status_code
        == 303
    )
