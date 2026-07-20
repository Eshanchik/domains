"""Companies / projects / tags: CRUD, scope filtering, RBAC, audit."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.audit import AuditLog
from app.models.company import Company, Project, Tag
from app.models.user import Role


def _run(coro):
    return asyncio.run(coro)


def _login(client, login_name, password):
    return client.post("/login", data={"login": login_name, "password": password})


def test_admin_creates_company_and_audits(client, make_user) -> None:
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    resp = client.post(
        "/companies", data={"name": "ACME Corp", "code": "acme"}, follow_redirects=False
    )
    assert resp.status_code == 303

    async def _check():
        async with SessionLocal() as s:
            company = (
                await s.execute(select(Company).where(Company.code == "acme"))
            ).scalar_one_or_none()
            audits = (
                await s.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.entity_type == "company", AuditLog.action == "create")
                )
            ).scalar_one()
            return company, audits

    company, audits = _run(_check())
    assert company is not None and company.name == "ACME Corp"
    assert audits == 1


def test_duplicate_company_code_is_rejected(client, make_user, make_company) -> None:
    make_company(code="acme")
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    resp = client.post("/companies", data={"name": "Dup", "code": "acme"}, follow_redirects=False)
    assert resp.status_code == 400
    assert "Код уже используется" in resp.text


def test_viewer_cannot_create_company(client, make_user) -> None:
    make_user(login="vic", password="password123", role=Role.viewer)
    _login(client, "vic", "password123")
    resp = client.post("/companies", data={"name": "X", "code": "x"}, follow_redirects=False)
    assert resp.status_code == 403


def test_project_scope_filters_lists(client, make_user, make_company, make_project) -> None:
    acme = make_company(code="acme")
    globex = make_company(code="globex")
    make_project(acme, code="web")
    make_project(globex, code="portal")

    # Manager scoped only to ACME.
    make_user(
        login="mgr",
        password="password123",
        role=Role.manager,
        scopes=[{"company_id": acme}],
    )
    _login(client, "mgr", "password123")

    companies_page = client.get("/companies")
    assert "acme" in companies_page.text
    assert "globex" not in companies_page.text

    projects_page = client.get("/projects")
    assert "web" in projects_page.text
    assert "portal" not in projects_page.text


def test_admin_sees_all_companies(client, make_user, make_company) -> None:
    make_company(code="acme")
    make_company(code="globex")
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")
    page = client.get("/companies")
    assert "acme" in page.text and "globex" in page.text


def test_admin_creates_project_under_company(client, make_user, make_company) -> None:
    acme = make_company(code="acme")
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    resp = client.post(
        "/projects",
        data={"company_id": str(acme), "name": "ACME Web", "code": "web"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    async def _count():
        async with SessionLocal() as s:
            return (await s.execute(select(func.count()).select_from(Project))).scalar_one()

    assert _run(_count()) == 1


def test_admin_manages_tags(client, make_user) -> None:
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    client.post("/tags", data={"name": "prod"}, follow_redirects=False)
    # Duplicate is silently ignored (no crash).
    client.post("/tags", data={"name": "prod"}, follow_redirects=False)

    async def _tags():
        async with SessionLocal() as s:
            return list((await s.execute(select(Tag))).scalars().all())

    tags = _run(_tags())
    assert len(tags) == 1 and tags[0].name == "prod"

    client.post(f"/tags/{tags[0].id}/delete", follow_redirects=False)
    assert _run(_tags()) == []
