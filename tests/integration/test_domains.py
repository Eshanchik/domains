"""Domain registry: dedup, IDN, field history, scope, bulk, CSV."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.domain import Domain, DomainFieldHistory
from app.models.user import Role


def _run(coro):
    return asyncio.run(coro)


def _login(client, name, pw):
    return client.post("/login", data={"login": name, "password": pw})


def _admin(client, make_user):
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")


def test_create_domain_and_dedup(client, make_user, make_company, make_project) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    _admin(client, make_user)

    resp = client.post(
        "/domains", data={"fqdn": "Example.COM", "project_id": str(proj)}, follow_redirects=False
    )
    assert resp.status_code == 303

    # Re-adding the same FQDN (different case) is a duplicate.
    dup = client.post(
        "/domains", data={"fqdn": "example.com", "project_id": str(proj)}, follow_redirects=False
    )
    assert dup.status_code == 400
    assert "уже есть" in dup.text

    async def _count():
        async with SessionLocal() as s:
            return (await s.execute(select(func.count()).select_from(Domain))).scalar_one()

    assert _run(_count()) == 1


def test_idn_dedup_across_forms(client, make_user, make_company, make_project) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    _admin(client, make_user)

    client.post("/domains", data={"fqdn": "münchen.de", "project_id": str(proj)})
    dup = client.post(
        "/domains",
        data={"fqdn": "xn--mnchen-3ya.de", "project_id": str(proj)},
        follow_redirects=False,
    )
    assert dup.status_code == 400  # same canonical domain


def test_viewer_cannot_create_domain(client, make_user, make_company, make_project) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    make_user(login="vic", password="password123", role=Role.viewer, scopes=[{"company_id": acme}])
    _login(client, "vic", "password123")
    resp = client.post(
        "/domains", data={"fqdn": "x.com", "project_id": str(proj)}, follow_redirects=False
    )
    assert resp.status_code == 403


def test_manager_out_of_scope_cannot_create(client, make_user, make_company, make_project) -> None:
    acme = make_company(code="acme")
    globex = make_company(code="globex")
    proj_globex = make_project(globex, code="portal")
    # Manager scoped to ACME only.
    make_user(login="mgr", password="password123", role=Role.manager, scopes=[{"company_id": acme}])
    _login(client, "mgr", "password123")
    resp = client.post(
        "/domains", data={"fqdn": "x.com", "project_id": str(proj_globex)}, follow_redirects=False
    )
    assert resp.status_code == 403


def test_field_history_recorded_on_expiry_change(
    client, make_user, make_company, make_project, make_domain
) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    _admin(client, make_user)

    client.post(
        f"/domains/{dom}",
        data={"expiry_date": "2027-01-15", "tags": "", "ssl_extra_hosts": "", "notes": ""},
        follow_redirects=False,
    )

    async def _history():
        async with SessionLocal() as s:
            rows = (
                (
                    await s.execute(
                        select(DomainFieldHistory).where(DomainFieldHistory.field == "expiry_date")
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    history = _run(_history())
    assert len(history) == 1
    assert "2027-01-15" in history[0].new

    # The card must render (history is eager-loaded, not lazily during template render).
    card = client.get(f"/domains/{dom}")
    assert card.status_code == 200
    assert "example.com" in card.text
    assert "expiry_date" in card.text  # history row visible


def test_scope_filters_domain_list(
    client, make_user, make_company, make_project, make_domain
) -> None:
    acme = make_company(code="acme")
    globex = make_company(code="globex")
    pa = make_project(acme, code="web")
    pg = make_project(globex, code="portal")
    make_domain(pa, fqdn="acme-domain.com")
    make_domain(pg, fqdn="globex-domain.com")

    make_user(login="mgr", password="password123", role=Role.manager, scopes=[{"company_id": acme}])
    _login(client, "mgr", "password123")
    page = client.get("/domains")
    assert "acme-domain.com" in page.text
    assert "globex-domain.com" not in page.text


def test_bulk_archive_and_csv_export(
    client, make_user, make_company, make_project, make_domain
) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    d1 = make_domain(proj, fqdn="one.com", expiry_date=datetime(2027, 1, 1, tzinfo=UTC))
    make_domain(proj, fqdn="two.com")
    _admin(client, make_user)

    # CSV export includes both active domains.
    csv_resp = client.get("/domains/export.csv")
    assert csv_resp.status_code == 200
    assert "one.com" in csv_resp.text and "two.com" in csv_resp.text

    # Bulk-archive one domain; it then drops out of the default (active-only) list.
    client.post("/domains/bulk", data={"action": "archive", "ids": [d1]}, follow_redirects=False)
    page = client.get("/domains")
    assert "one.com" not in page.text
    assert "two.com" in page.text


# --- T25: list columns (project name, SSL, auto-renew) + row actions -----------


def _add_ssl(domain_id: int, *, valid_to=None, error=None) -> None:
    from app.models.ssl_certificate import SslCertificate

    async def _c() -> None:
        async with SessionLocal() as s:
            s.add(SslCertificate(domain_id=domain_id, host="h", valid_to=valid_to, error=error))
            await s.commit()

    _run(_c())


def test_ssl_status_map_classifies_latest_cert(make_company, make_project, make_domain) -> None:
    from datetime import timedelta

    from app.services import domains as svc

    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    now = datetime.now(UTC)
    ok = make_domain(proj, fqdn="ok.com")
    soon = make_domain(proj, fqdn="soon.com")
    expired = make_domain(proj, fqdn="expired.com")
    broken = make_domain(proj, fqdn="broken.com")
    none = make_domain(proj, fqdn="none.com")
    _add_ssl(ok, valid_to=now + timedelta(days=90))
    _add_ssl(soon, valid_to=now + timedelta(days=5))
    _add_ssl(expired, valid_to=now - timedelta(days=1))
    _add_ssl(broken, error="handshake failed")

    async def _c():
        async with SessionLocal() as s:
            return await svc.ssl_status_map(s, [ok, soon, expired, broken, none])

    m = _run(_c())
    assert m[ok] == ("ok", "green")
    assert m[soon] == ("скоро", "amber")
    assert m[expired] == ("истёк", "red")
    assert m[broken] == ("проблема", "red")
    assert none not in m  # no observation → omitted, list shows «—»


def test_request_immediate_checks_enqueues_and_audits(
    make_user, make_company, make_project, make_domain
) -> None:
    from sqlalchemy import select

    from app.models.audit import AuditLog
    from app.models.domain import Domain
    from app.services import domains as svc

    actor = make_user(login="root", password="password123", role=Role.admin)
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    did = make_domain(proj, fqdn="chk.com")
    sent: list[tuple[int, str]] = []

    async def _c():
        async with SessionLocal() as s:
            dom = await s.get(Domain, did)
            types = await svc.request_immediate_checks(
                s, dom, actor_id=actor["id"], send=lambda d, t: sent.append((d, t))
            )
            audit = (
                (await s.execute(select(AuditLog).where(AuditLog.action == "check_now")))
                .scalars()
                .all()
            )
            return types, audit

    types, audit = _run(_c())
    assert set(types) == {"rdap", "ssl", "vt", "dns"}
    assert sorted(sent) == sorted((did, t) for t in types)
    assert len(audit) == 1


def test_domains_list_shows_new_columns(
    client, make_user, make_company, make_project, make_domain
) -> None:
    from datetime import timedelta

    acme = make_company(code="acme")
    proj = make_project(acme, code="web", name="ACME Web")
    d = make_domain(proj, fqdn="col.com", auto_renew=None)
    _add_ssl(d, valid_to=datetime.now(UTC) + timedelta(days=200))
    _admin(client, make_user)

    page = client.get("/domains")
    assert page.status_code == 200
    assert "ACME Web" in page.text  # project by NAME, not id
    assert "неизвестно" in page.text  # auto_renew=None label
    assert "Auto-renew" in page.text  # new column header
    assert "Проверить сейчас" in page.text  # kebab row action


def test_check_now_endpoint_enqueues_admin_only(
    client, make_user, make_company, make_project, make_domain, monkeypatch
) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    did = make_domain(proj, fqdn="run.com")
    sent: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "app.services.domains._default_check_sender",
        lambda d, t: sent.append((d, t)),
    )

    # Viewer is blocked (Manager+ required) and enqueues nothing.
    make_user(login="viewer", password="password123", role=Role.viewer)
    _login(client, "viewer", "password123")
    denied = client.post(f"/domains/{did}/check", follow_redirects=False)
    assert denied.status_code == 403
    assert sent == []

    # Admin enqueues all default checks.
    _admin(client, make_user)
    resp = client.post(f"/domains/{did}/check", follow_redirects=False)
    assert resp.status_code == 303
    assert sorted(sent) == sorted((did, t) for t in ("rdap", "ssl", "vt", "dns"))
