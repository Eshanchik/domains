"""REST API v1 token auth + scoping."""

from __future__ import annotations

import asyncio

from app.db import SessionLocal
from app.models.user import Role, User
from app.services import api_tokens


def _run(coro):
    return asyncio.run(coro)


def _make_token(user_id: int, name: str = "t") -> str:
    async def _c() -> str:
        async with SessionLocal() as s:
            user = await s.get(User, user_id)
            _tok, plaintext = await api_tokens.create_token(s, user, name)
            return plaintext

    return _run(_c())


def test_me_requires_token(client, make_user):
    u = make_user(login="dev", role=Role.viewer)
    assert client.get("/api/v1/me").status_code == 401  # no token

    token = _make_token(u["id"])
    resp = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["login"] == "dev"


def test_invalid_token_rejected(client, make_user):
    make_user(login="dev", role=Role.viewer)
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer dg_nope"}).status_code == 401


def test_revoked_token_rejected(client, make_user):
    u = make_user(login="dev", role=Role.viewer)
    token = _make_token(u["id"])
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    async def _revoke():
        async with SessionLocal() as s:
            from sqlalchemy import select

            from app.models.api import ApiToken

            t = (await s.execute(select(ApiToken))).scalar_one()
            await api_tokens.revoke(s, t, actor_id=u["id"])

    _run(_revoke())
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_api_domains_scoped(client, make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    globex = make_company(code="globex")
    pa = make_project(acme, code="web")
    pg = make_project(globex, code="portal")
    make_domain(pa, fqdn="acme-a.com")
    make_domain(pg, fqdn="globex-b.com")

    u = make_user(login="mgr", role=Role.manager, scopes=[{"company_id": acme}])
    token = _make_token(u["id"])
    resp = client.get("/api/v1/domains", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    fqdns = {d["fqdn"] for d in resp.json()["items"]}
    assert fqdns == {"acme-a.com"}  # only the scoped company's domain
