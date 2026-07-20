"""Auth flows: login, lockout, sessions, RBAC, and audit logging."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.core import login_guard
from app.db import SessionLocal
from app.models.audit import AuditLog
from app.models.user import Role, User


def _run(coro):
    return asyncio.run(coro)


def test_login_page_renders(client) -> None:
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Вход" in resp.text


def test_home_redirects_anonymous_to_login(client) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_login_wrong_password_is_401(client, make_user) -> None:
    make_user(login="alice", password="password123")
    resp = client.post(
        "/login",
        data={"login": "alice", "password": "WRONG"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert "Неверный логин или пароль" in resp.text


def test_login_success_sets_session_and_grants_access(client, make_user) -> None:
    make_user(login="bob", password="password123")
    resp = client.post(
        "/login",
        data={"login": "bob", "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert "dg_session" in resp.cookies

    home = client.get("/")
    assert home.status_code == 200
    assert "bob" in home.text


def test_logout_destroys_session(client, make_user) -> None:
    make_user(login="carol", password="password123")
    client.post("/login", data={"login": "carol", "password": "password123"})
    assert client.get("/", follow_redirects=False).status_code == 200

    out = client.post("/logout", follow_redirects=False)
    assert out.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303  # back to login


def test_lockout_after_max_failed_attempts(client, make_user) -> None:
    make_user(login="dave", password="password123")
    # Exhaust the allowed attempts with wrong passwords.
    for _ in range(login_guard.MAX_ATTEMPTS):
        resp = client.post(
            "/login", data={"login": "dave", "password": "nope"}, follow_redirects=False
        )
    assert resp.status_code == 401
    assert "Слишком много" in resp.text

    # Even the correct password is now refused while locked out.
    locked = client.post(
        "/login", data={"login": "dave", "password": "password123"}, follow_redirects=False
    )
    assert locked.status_code == 401
    assert "Слишком много" in locked.text


def test_viewer_cannot_access_user_admin(client, make_user) -> None:
    make_user(login="vic", password="password123", role=Role.viewer)
    client.post("/login", data={"login": "vic", "password": "password123"})
    resp = client.get("/users", follow_redirects=False)
    assert resp.status_code == 403


def test_admin_creates_user_and_writes_audit(client, make_user, make_company, make_project) -> None:
    company_id = make_company(code="acme")
    project_id = make_project(company_id, code="web")
    make_user(login="root", password="password123", role=Role.admin)
    client.post("/login", data={"login": "root", "password": "password123"})

    assert client.get("/users").status_code == 200

    resp = client.post(
        "/users",
        data={
            "email": "newbie@example.com",
            "login": "newbie",
            "password": "password123",
            "role": "manager",
            "scopes": f"company:{company_id}\nproject:{project_id}",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    async def _check() -> tuple[User | None, int]:
        async with SessionLocal() as s:
            user = (
                await s.execute(select(User).where(User.login == "newbie"))
            ).scalar_one_or_none()
            audit_count = (
                await s.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "create", AuditLog.entity_type == "user")
                )
            ).scalar_one()
            return user, audit_count

    user, audit_count = _run(_check())
    assert user is not None
    assert user.role == Role.manager
    assert len(user.scopes) == 2
    assert audit_count >= 1
