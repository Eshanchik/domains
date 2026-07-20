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
