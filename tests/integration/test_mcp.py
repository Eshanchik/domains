"""MCP tools: read scoping, Manager+ gating, mutations + audit, token auth."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.db import SessionLocal
from app.mcp import tools
from app.models.alert import AlertEvent
from app.models.audit import AuditLog
from app.models.domain import Domain
from app.models.user import Role, User
from app.services import api_tokens


def _run(coro):
    return asyncio.run(coro)


async def _user(session, uid: int) -> User:
    return await session.get(User, uid)


def test_whoami_and_list_domains_scoped(make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    other = make_company(code="other")
    p1 = make_project(acme, code="web")
    p2 = make_project(other, code="web")
    make_domain(p1, fqdn="acme-a.com")
    make_domain(p2, fqdn="other-b.com")
    # Viewer scoped to the acme company only.
    info = make_user(login="v", role=Role.viewer, scopes=[{"company_id": acme}])

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, info["id"])
            who = await tools.whoami(s, u)
            listing = await tools.list_domains(s, u)
            return who, listing

    who, listing = _run(run())
    assert who["role"] == "viewer"
    fqdns = {d["fqdn"] for d in listing["items"]}
    assert fqdns == {"acme-a.com"}  # out-of-scope domain hidden


def test_create_domain_requires_manager(make_user, make_company, make_project):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    viewer = make_user(login="v", role=Role.viewer)

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, viewer["id"])
            try:
                await tools.create_domain(s, u, fqdn="new.com", project_id=proj)
                return "created"
            except tools.ToolPermissionError:
                return "denied"

    assert _run(run()) == "denied"


def test_admin_create_domain_and_audit(make_user, make_company, make_project):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    admin = make_user(login="a", role=Role.admin)

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, admin["id"])
            created = await tools.create_domain(
                s, u, fqdn="fresh.com", project_id=proj, tags=["prod"]
            )
        async with SessionLocal() as s:
            in_db = (
                await s.execute(select(Domain).where(Domain.fqdn == "fresh.com"))
            ).scalar_one_or_none()
            audits = (
                await s.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.entity_type == "domain", AuditLog.action == "create")
                )
            ).scalar_one()
            return created, in_db, audits

    created, in_db, audits = _run(run())
    assert created["fqdn"] == "fresh.com"
    assert in_db is not None
    assert audits == 1  # mutation recorded in the audit log


def test_get_domain_out_of_scope_rejected(make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    other = make_company(code="other")
    p_other = make_project(other, code="web")
    did = make_domain(p_other, fqdn="secret.com")
    viewer = make_user(login="v", role=Role.viewer, scopes=[{"company_id": acme}])

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, viewer["id"])
            try:
                await tools.get_domain(s, u, domain_id=did)
                return "leaked"
            except tools.ToolInputError:
                return "blocked"

    assert _run(run()) == "blocked"


def test_check_domain_now_enqueues(make_user, make_company, make_project, make_domain, monkeypatch):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    did = make_domain(proj, fqdn="check.com")
    admin = make_user(login="a", role=Role.admin)
    sent: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "app.services.domains._default_check_sender",
        lambda domain_id, check_type: sent.append((domain_id, check_type)),
    )

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, admin["id"])
            return await tools.check_domain_now(s, u, domain_id=did)

    result = _run(run())
    assert set(result["enqueued"]) == {"rdap", "ssl", "vt", "dns"}
    assert sorted(sent) == sorted((did, t) for t in ("rdap", "ssl", "vt", "dns"))


def test_resolve_alert_closes_event(make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    did = make_domain(proj, fqdn="al.com")
    admin = make_user(login="a", role=Role.admin)

    async def seed_alert() -> int:
        async with SessionLocal() as s:
            ev = AlertEvent(
                domain_id=did,
                kind="ssl",
                dedupe_key=f"{did}:ssl",
                severity="high",
                state="active",
            )
            s.add(ev)
            await s.commit()
            await s.refresh(ev)
            return ev.id

    aid = _run(seed_alert())

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, admin["id"])
            res = await tools.resolve_alert(s, u, alert_id=aid)
        async with SessionLocal() as s:
            state = (await s.get(AlertEvent, aid)).state
            return res, state

    res, state = _run(run())
    assert res["resolved"] is True and state == "resolved"


def test_import_domains_dry_run(make_user, make_company, make_project):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    admin = make_user(login="a", role=Role.admin)

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, admin["id"])
            report = await tools.import_domains(
                s,
                u,
                text="imp-one.com\nimp-two.com",
                default_project_id=proj,
                dry_run=True,
            )
        async with SessionLocal() as s:
            count = (await s.execute(select(func.count()).select_from(Domain))).scalar_one()
            return report, count

    report, count = _run(run())
    assert report["dry_run"] is True and report["created"] == 2
    assert count == 0  # dry run persisted nothing


# --- OAuth provider token auth -----------------------------------------------


def _make_token(user_id: int) -> str:
    async def _c() -> str:
        async with SessionLocal() as s:
            user = await s.get(User, user_id)
            _, plaintext = await api_tokens.create_token(s, user, "mcp")
            return plaintext

    return _run(_c())


def test_load_access_token_accepts_api_token(make_user):
    from app.mcp.oauth_provider import DomainGuardOAuthProvider

    info = make_user(login="tok", role=Role.admin)
    token = _make_token(info["id"])
    provider = DomainGuardOAuthProvider()

    # A valid DomainGuard API token resolves to an access token bound to the user.
    at = _run(provider.load_access_token(token))
    assert at is not None and at.subject == str(info["id"])
    # Bad/empty tokens do not.
    assert _run(provider.load_access_token("dg_nope")) is None
    assert _run(provider.load_access_token("")) is None


def test_oauth_token_roundtrip_via_store():
    from mcp.server.auth.provider import AccessToken

    from app.mcp import oauth_store as store
    from app.mcp.oauth_provider import DomainGuardOAuthProvider

    # Issue + read back an OAuth access token through the store.
    _run(store.put_access(AccessToken(token="acc123", client_id="c1", scopes=["mcp"], subject="7")))
    got = _run(DomainGuardOAuthProvider().load_access_token("acc123"))
    assert got is not None and got.subject == "7"
