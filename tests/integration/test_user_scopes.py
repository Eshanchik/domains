"""Per-user access scope (T52): checkbox assignment UI + enforcement gaps.

Covers the friendly scope-assignment form (companies/projects checkboxes) and the
two write-side scope holes closed in T52:
- bulk "assign to project" must not move domains into an out-of-scope project;
- import must not mutate an existing domain living outside the caller's scope.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db import SessionLocal
from app.models.domain import Domain
from app.models.user import Role, User


def _run(coro):
    return asyncio.run(coro)


def _login(client, name, pw):
    return client.post("/login", data={"login": name, "password": pw}, follow_redirects=False)


def _get_user(login: str) -> User:
    async def _q() -> User:
        async with SessionLocal() as s:
            return (await s.execute(select(User).where(User.login == login))).scalar_one()

    return _run(_q())


def _get_domain(domain_id: int) -> Domain:
    async def _q() -> Domain:
        async with SessionLocal() as s:
            return await s.get(Domain, domain_id)

    return _run(_q())


# --- checkbox assignment UI ------------------------------------------------


def test_create_user_with_company_checkbox(client, make_user, make_company, make_project):
    gt1 = make_company(code="gt1")
    make_project(gt1, code="main")
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    # New-user form lists the company as a checkbox option.
    form = client.get("/users/new")
    assert form.status_code == 200
    assert 'name="company_scopes"' in form.text
    assert "gt1" in form.text

    resp = client.post(
        "/users",
        data={
            "email": "scoped@example.com",
            "login": "scoped",
            "password": "password123",
            "role": "manager",
            "company_scopes": [gt1],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    user = _get_user("scoped")
    assert len(user.scopes) == 1
    assert user.scopes[0].company_id == gt1
    assert user.scopes[0].project_id is None


def test_edit_user_replaces_scopes(client, make_user, make_company, make_project):
    gt1 = make_company(code="gt1")
    other = make_company(code="other")
    proj_other = make_project(other, code="p")
    make_user(login="root", password="password123", role=Role.admin)
    subject = make_user(
        login="edit-me", password="password123", role=Role.manager, scopes=[{"company_id": gt1}]
    )
    _login(client, "root", "password123")

    # The edit form pre-checks the currently granted company.
    form = client.get(f"/users/{subject['id']}/edit")
    assert form.status_code == 200

    # Replace the whole set with a single project scope on a different company.
    resp = client.post(
        f"/users/{subject['id']}",
        data={
            "email": "edit-me@example.com",
            "role": "manager",
            "is_active": "on",
            "project_scopes": [proj_other],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    user = _get_user("edit-me")
    assert len(user.scopes) == 1
    assert user.scopes[0].project_id == proj_other
    assert user.scopes[0].company_id is None


def test_edit_user_can_clear_all_scopes(client, make_user, make_company):
    gt1 = make_company(code="gt1")
    make_user(login="root", password="password123", role=Role.admin)
    subject = make_user(
        login="clearme", password="password123", role=Role.manager, scopes=[{"company_id": gt1}]
    )
    _login(client, "root", "password123")

    resp = client.post(
        f"/users/{subject['id']}",
        data={"email": "clearme@example.com", "role": "manager", "is_active": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert len(_get_user("clearme").scopes) == 0


# --- read enforcement (end-to-end "sees only their company") ---------------


def test_scoped_user_sees_only_their_company(
    client, make_user, make_company, make_project, make_domain
):
    gt1 = make_company(code="gt1")
    gt1_proj = make_project(gt1, code="main")
    mine = make_domain(gt1_proj, fqdn="mine.gt1")

    other = make_company(code="other")
    other_proj = make_project(other, code="p")
    foreign = make_domain(other_proj, fqdn="foreign.other")

    make_user(
        login="gt1user", password="password123", role=Role.manager, scopes=[{"company_id": gt1}]
    )
    _login(client, "gt1user", "password123")

    listing = client.get("/domains")
    assert listing.status_code == 200
    assert "mine.gt1" in listing.text
    assert "foreign.other" not in listing.text

    # Direct access to the out-of-scope domain redirects away (no foreign data).
    card = client.get(f"/domains/{foreign}", follow_redirects=False)
    assert card.status_code in (302, 303)
    # In-scope domain opens normally.
    assert client.get(f"/domains/{mine}").status_code == 200


# --- write enforcement: bulk assign to a foreign project -------------------


def test_scoped_manager_cannot_bulk_assign_to_foreign_project(
    client, make_user, make_company, make_project, make_domain
):
    gt1 = make_company(code="gt1")
    gt1_proj = make_project(gt1, code="main")
    dom = make_domain(gt1_proj, fqdn="movable.gt1")

    other = make_company(code="other")
    foreign_proj = make_project(other, code="p")

    make_user(
        login="gt1mgr", password="password123", role=Role.manager, scopes=[{"company_id": gt1}]
    )
    _login(client, "gt1mgr", "password123")

    resp = client.post(
        "/domains/bulk",
        data={"action": "assign_project", "ids": [dom], "project_id": foreign_proj},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    # The domain stayed in its original (in-scope) project — no cross-scope move.
    assert _get_domain(dom).project_id == gt1_proj


def test_scoped_manager_can_bulk_assign_within_scope(
    client, make_user, make_company, make_project, make_domain
):
    gt1 = make_company(code="gt1")
    proj_a = make_project(gt1, code="a")
    proj_b = make_project(gt1, code="b")
    dom = make_domain(proj_a, fqdn="within.gt1")

    make_user(
        login="gt1mgr2", password="password123", role=Role.manager, scopes=[{"company_id": gt1}]
    )
    _login(client, "gt1mgr2", "password123")

    client.post(
        "/domains/bulk",
        data={"action": "assign_project", "ids": [dom], "project_id": proj_b},
        follow_redirects=False,
    )
    assert _get_domain(dom).project_id == proj_b  # both projects are in scope → allowed


# --- write enforcement: import must not touch a foreign existing domain -----


def test_import_refuses_to_update_out_of_scope_domain(
    client, make_user, make_company, make_project, make_domain
):
    gt1 = make_company(code="gt1")
    gt1_proj = make_project(gt1, code="main")

    other = make_company(code="other")
    other_proj = make_project(other, code="p")
    foreign = make_domain(other_proj, fqdn="shared.example")

    make_user(
        login="gt1imp", password="password123", role=Role.manager, scopes=[{"company_id": gt1}]
    )
    _login(client, "gt1imp", "password123")

    # CSV row targets an in-scope default project but matches an existing FQDN that
    # lives in a foreign project; the update must be refused (not silently applied).
    csv_text = "fqdn,notes\nshared.example,hacked"
    resp = client.post(
        "/import",
        data={
            "content": csv_text,
            "fmt": "csv",
            "default_project_id": str(gt1_proj),
            "commit": "true",
        },
    )
    assert resp.status_code == 200
    assert "вне доступа" in resp.text
    # The foreign domain was not mutated or re-homed.
    after = _get_domain(foreign)
    assert after.project_id == other_proj
    assert after.notes != "hacked"
