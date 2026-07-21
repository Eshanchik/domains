"""Access-scope resolution (pure, no DB)."""

from __future__ import annotations

from app.models.user import Role, User, UserScope
from app.services.auth import user_in_scope, user_may_use_mcp


def _user(role: Role, scopes: list[UserScope]) -> User:
    u = User(email="x@e.com", login="x", password_hash="x", role=role, is_active=True)
    u.scopes = scopes
    return u


def test_admin_always_in_scope() -> None:
    u = _user(Role.admin, [])
    assert user_in_scope(u, company_id=1, project_id=99) is True


def test_mcp_allowed_admin_always() -> None:
    admin = _user(Role.admin, [])
    admin.mcp_allowed = False
    assert user_may_use_mcp(admin) is True  # admins bypass the flag


def test_mcp_allowed_flag_gates_non_admin() -> None:
    viewer = _user(Role.viewer, [])
    viewer.mcp_allowed = False
    assert user_may_use_mcp(viewer) is False
    viewer.mcp_allowed = True
    assert user_may_use_mcp(viewer) is True


def test_non_admin_without_scopes_denied() -> None:
    u = _user(Role.viewer, [])
    assert user_in_scope(u, company_id=1, project_id=1) is False


def test_company_scope_covers_projects_of_that_company() -> None:
    u = _user(Role.manager, [UserScope(company_id=5)])
    assert user_in_scope(u, company_id=5, project_id=42) is True
    assert user_in_scope(u, company_id=6, project_id=42) is False


def test_project_scope_matches_single_project() -> None:
    u = _user(Role.viewer, [UserScope(project_id=7)])
    assert user_in_scope(u, company_id=None, project_id=7) is True
    assert user_in_scope(u, company_id=None, project_id=8) is False
