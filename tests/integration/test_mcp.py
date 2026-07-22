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


# --- T53: health-checks, domain edit, payments, structure --------------------


def test_health_check_add_list_delete(make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    did = make_domain(proj, fqdn="hc.com")
    admin = make_user(login="a", role=Role.admin)

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, admin["id"])
            created = await tools.add_health_check(
                s, u, domain_id=did, url="https://hc.com/click?pid=1&offer_id=625"
            )
            listed = await tools.list_health_checks(s, u, domain_id=did)
            deleted = await tools.delete_health_check(s, u, healthcheck_id=created["id"])
            after = await tools.list_health_checks(s, u, domain_id=did)
            return created, listed, deleted, after

    created, listed, deleted, after = _run(run())
    assert created["method"] == "GET" and created["expected_statuses"] == "200-299"
    assert len(listed["items"]) == 1
    assert deleted["deleted"] is True
    assert after["items"] == []


def test_add_health_check_requires_manager(make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    did = make_domain(proj, fqdn="hcv.com")
    viewer = make_user(login="v", role=Role.viewer, scopes=[{"company_id": acme}])

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, viewer["id"])
            try:
                await tools.add_health_check(s, u, domain_id=did, url="https://hcv.com/x")
                return "added"
            except tools.ToolPermissionError:
                return "denied"

    assert _run(run()) == "denied"


def test_add_health_check_out_of_scope_rejected(
    make_user, make_company, make_project, make_domain
):
    acme = make_company(code="acme")
    other = make_company(code="other")
    p_other = make_project(other, code="web")
    did = make_domain(p_other, fqdn="foreign-hc.com")
    mgr = make_user(login="m", role=Role.manager, scopes=[{"company_id": acme}])

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, mgr["id"])
            try:
                await tools.add_health_check(s, u, domain_id=did, url="https://foreign-hc.com/x")
                return "leaked"
            except tools.ToolInputError:
                return "blocked"

    assert _run(run()) == "blocked"


def test_bulk_add_health_check_scoped(make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    other = make_company(code="other")
    p_acme = make_project(acme, code="web")
    p_other = make_project(other, code="web")
    d_mine = make_domain(p_acme, fqdn="mine.com")
    d_foreign = make_domain(p_other, fqdn="theirs.com")
    mgr = make_user(login="m", role=Role.manager, scopes=[{"company_id": acme}])

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, mgr["id"])
            res = await tools.bulk_add_health_check(
                s,
                u,
                domain_ids=[d_mine, d_foreign],
                url_template="https://{fqdn}/click?pid=1&offer_id=625",
            )
            mine = await tools.list_health_checks(s, u, domain_id=d_mine)
            return res, mine

    res, mine = _run(run())
    assert res["applied"] == 1 and res["skipped"] == [d_foreign]
    assert mine["items"][0]["url"] == "https://mine.com/click?pid=1&offer_id=625"


def test_update_domain_edits_fields(make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    did = make_domain(proj, fqdn="edit.com")
    admin = make_user(login="a", role=Role.admin)

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, admin["id"])
            return await tools.update_domain(
                s, u, domain_id=did, notes="ping", auto_renew=True, tags=["prod"]
            )

    out = _run(run())
    assert out["notes"] == "ping" and out["auto_renew"] is True


def test_update_domain_project_move_out_of_scope_denied(
    make_user, make_company, make_project, make_domain
):
    acme = make_company(code="acme")
    other = make_company(code="other")
    p_acme = make_project(acme, code="web")
    p_other = make_project(other, code="web")
    did = make_domain(p_acme, fqdn="movable.com")
    mgr = make_user(login="m", role=Role.manager, scopes=[{"company_id": acme}])

    async def run():
        async with SessionLocal() as s:
            u = await _user(s, mgr["id"])
            try:
                await tools.update_domain(s, u, domain_id=did, project_id=p_other)
                return "moved"
            except tools.ToolPermissionError:
                return "denied"

    assert _run(run()) == "denied"


def test_payment_add_and_list(make_user, make_company, make_project, make_domain):
    from app.db import get_redis

    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    did = make_domain(proj, fqdn="pay.com")
    admin = make_user(login="a", role=Role.admin)

    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                u = await _user(s, admin["id"])
                added = await tools.add_payment(
                    s, u, redis, domain_id=did, amount="12.50", currency="USD"
                )
                listed = await tools.list_payments(s, u, domain_id=did)
                return added, listed
        finally:
            await redis.aclose()

    added, listed = _run(run())
    assert added["amount_usd"] == "12.50"
    assert len(listed["items"]) == 1 and listed["items"][0]["currency"] == "USD"


def test_create_company_and_project_admin_only(make_user, make_company):
    manager = make_user(login="m", role=Role.manager)
    admin = make_user(login="a", role=Role.admin)

    async def denied():
        async with SessionLocal() as s:
            u = await _user(s, manager["id"])
            try:
                await tools.create_company(s, u, code="nope", name="Nope")
                return "created"
            except tools.ToolPermissionError:
                return "denied"

    async def created():
        async with SessionLocal() as s:
            u = await _user(s, admin["id"])
            c = await tools.create_company(s, u, code="newco", name="NewCo")
            p = await tools.create_project(s, u, company_id=c["id"], code="main", name="Main")
            return c, p

    assert _run(denied()) == "denied"
    c, p = _run(created())
    assert c["code"] == "newco" and p["company_id"] == c["id"] and p["code"] == "main"


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
