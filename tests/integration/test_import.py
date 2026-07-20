"""Domain import: preview/commit, upsert, manual preservation, errors, scope."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.domain import Domain
from app.models.user import Role


def _run(coro):
    return asyncio.run(coro)


def _login(client, name, pw):
    return client.post("/login", data={"login": name, "password": pw})


def _count_domains() -> int:
    async def _c():
        async with SessionLocal() as s:
            return (await s.execute(select(func.count()).select_from(Domain))).scalar_one()

    return _run(_c())


def test_preview_does_not_persist_then_commit_does(client, make_user, make_company, make_project):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    payload = {"content": "one.com\ntwo.com", "fmt": "bulk", "default_project_id": str(proj)}

    preview = client.post("/import", data={**payload, "commit": "false"})
    assert preview.status_code == 200
    assert "Создано: <b" in preview.text or "Создано:" in preview.text
    assert _count_domains() == 0  # dry-run persisted nothing

    committed = client.post("/import", data={**payload, "commit": "true"})
    assert committed.status_code == 200
    assert _count_domains() == 2


def test_reimport_upserts_not_duplicates(client, make_user, make_company, make_project):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    payload = {
        "content": "dup.com",
        "fmt": "bulk",
        "default_project_id": str(proj),
        "commit": "true",
    }
    client.post("/import", data=payload)
    client.post("/import", data=payload)
    assert _count_domains() == 1


def test_invalid_line_reported_others_processed(client, make_user, make_company, make_project):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    resp = client.post(
        "/import",
        data={
            "content": "good.com\nnodot\nother.com",
            "fmt": "bulk",
            "default_project_id": str(proj),
            "commit": "true",
        },
    )
    assert "ошибка" in resp.text
    assert _count_domains() == 2  # good.com + other.com


def test_csv_import_with_project_code_and_tags(client, make_user, make_company, make_project):
    acme = make_company(code="acme")
    make_project(acme, code="web")
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    csv_text = 'fqdn,project_code,tags,renewal_price,currency\nshop.com,web,"prod,eu",9.99,EUR\n'
    resp = client.post(
        "/import",
        data={"content": csv_text, "fmt": "csv", "default_project_id": "", "commit": "true"},
    )
    assert resp.status_code == 200

    async def _fetch():
        async with SessionLocal() as s:
            d = (await s.execute(select(Domain).where(Domain.fqdn == "shop.com"))).scalar_one()
            return d.renewal_currency, {t.name for t in d.tags}

    currency, tags = _run(_fetch())
    assert currency == "EUR"
    assert tags == {"prod", "eu"}


def test_import_does_not_overwrite_manual_fields(
    client, make_user, make_company, make_project, make_domain
):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    # Domain with a manually-set note.
    dom = make_domain(
        proj,
        fqdn="manual.com",
        notes="keep me",
        field_sources={"fqdn": "manual", "notes": "manual"},
    )
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    csv_text = "fqdn,notes\nmanual.com,overwritten\n"
    client.post(
        "/import",
        data={"content": csv_text, "fmt": "csv", "default_project_id": str(proj), "commit": "true"},
    )

    async def _note():
        async with SessionLocal() as s:
            return (await s.execute(select(Domain.notes).where(Domain.id == dom))).scalar_one()

    assert _run(_note()) == "keep me"  # manual note preserved


def test_manager_import_out_of_scope_project_errors(client, make_user, make_company, make_project):
    acme = make_company(code="acme")
    globex = make_company(code="globex")
    pg = make_project(globex, code="portal")
    make_user(login="mgr", password="password123", role=Role.manager, scopes=[{"company_id": acme}])
    _login(client, "mgr", "password123")

    resp = client.post(
        "/import",
        data={"content": "x.com", "fmt": "bulk", "default_project_id": str(pg), "commit": "true"},
    )
    # Out-of-scope project → row error, nothing created.
    assert "ошибка" in resp.text
    assert _count_domains() == 0
