"""Integration fixtures: real Postgres + Redis (provided by CI / local compose).

Creates the schema once, truncates between tests, and offers a user factory and a
logged-in client helper. If the datastores are unreachable, all integration tests
skip (so ``make test`` does not hard-fail without a running stack).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from sqlalchemy import text

from app.core.security import hash_password
from app.db import SessionLocal, engine, get_redis
from app.models import Base
from app.models.company import Company, Project
from app.models.user import Role, User, UserScope


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="session", autouse=True)
def _setup_schema():
    async def setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    try:
        _run(setup())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"datastores not reachable: {exc.__class__.__name__}")
    yield


@pytest.fixture(autouse=True)
def _clean_state():
    async def clean() -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE users, user_scopes, audit_log, companies, projects, tags, "
                    "domains, domain_tags, domain_field_history, check_schedule, "
                    "check_result, ssl_certificates, vt_results, settings, "
                    "health_checks, health_check_results, notification_channels, "
                    "notification_log, alert_events, alert_rules, payments "
                    "RESTART IDENTITY CASCADE"
                )
            )
        redis = get_redis()
        try:
            await redis.flushdb()
        finally:
            await redis.aclose()

    _run(clean())
    yield


@pytest.fixture
def make_user() -> Callable[..., dict]:
    def _make(
        login: str = "user",
        password: str = "password123",
        role: Role = Role.viewer,
        email: str | None = None,
        scopes: list[dict] | None = None,
    ) -> dict:
        async def create() -> int:
            async with SessionLocal() as s:
                user = User(
                    email=email or f"{login}@example.com",
                    login=login,
                    password_hash=hash_password(password),
                    role=role,
                    is_active=True,
                )
                if scopes:
                    user.scopes = [UserScope(**sc) for sc in scopes]
                s.add(user)
                await s.commit()
                await s.refresh(user)
                return user.id

        uid = _run(create())
        return {"id": uid, "login": login, "password": password, "role": role}

    return _make


@pytest.fixture
def make_company() -> Callable[..., int]:
    def _make(code: str = "acme", name: str | None = None) -> int:
        async def create() -> int:
            async with SessionLocal() as s:
                c = Company(code=code, name=name or code.upper())
                s.add(c)
                await s.commit()
                await s.refresh(c)
                return c.id

        return _run(create())

    return _make


@pytest.fixture
def make_project() -> Callable[..., int]:
    def _make(company_id: int, code: str = "web", name: str | None = None) -> int:
        async def create() -> int:
            async with SessionLocal() as s:
                p = Project(company_id=company_id, code=code, name=name or code)
                s.add(p)
                await s.commit()
                await s.refresh(p)
                return p.id

        return _run(create())

    return _make


@pytest.fixture
def make_domain() -> Callable[..., int]:
    def _make(project_id: int, fqdn: str = "example.com", **kwargs) -> int:
        from app.core.fqdn import normalize_fqdn
        from app.models.domain import Domain

        async def create() -> int:
            norm = normalize_fqdn(fqdn)
            field_sources = kwargs.pop("field_sources", {"fqdn": "manual"})
            async with SessionLocal() as s:
                d = Domain(
                    project_id=project_id,
                    fqdn=norm.fqdn,
                    punycode=norm.punycode,
                    tld=norm.tld,
                    field_sources=field_sources,
                    **kwargs,
                )
                s.add(d)
                await s.commit()
                await s.refresh(d)
                return d.id

        return _run(create())

    return _make


@pytest.fixture
def login(client):
    """Return a helper that logs the shared client in as the given credentials."""

    def _login(login_name: str, password: str):
        resp = client.post(
            "/login",
            data={"login": login_name, "password": password},
            follow_redirects=False,
        )
        return resp

    return _login
