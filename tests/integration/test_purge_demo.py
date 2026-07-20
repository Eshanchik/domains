"""T27: safe, idempotent purge of seeded demo data (ACME/Globex)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.company import Company, Project
from app.models.domain import Domain


def _run(coro):
    return asyncio.run(coro)


async def _counts():
    async with SessionLocal() as s:
        companies = {c.code for c in (await s.execute(select(Company))).scalars()}
        projects = {
            (await s.get(Company, p.company_id)).code + "/" + p.code
            for p in (await s.execute(select(Project))).scalars()
        }
        domains = (await s.execute(select(func.count()).select_from(Domain))).scalar_one()
        return companies, projects, domains


def test_purge_removes_only_empty_demo(make_company, make_project, make_domain):
    from scripts.purge_demo import purge_demo

    acme = make_company(code="acme", name="ACME Corp")
    globex = make_company(code="globex", name="Globex")
    adera = make_company(code="AD", name="Adera")  # real, non-demo
    acme_web = make_project(acme, code="web", name="ACME Web")  # gets a real domain
    make_project(acme, code="blog", name="ACME Blog")  # empty demo project
    make_project(globex, code="portal", name="Globex Portal")  # empty demo project
    adera_p = make_project(adera, code="AD", name="Adera")
    make_domain(acme_web, fqdn="real-in-acme.com")
    make_domain(adera_p, fqdn="adera-domain.com")

    async def _purge():
        async with SessionLocal() as s:
            return await purge_demo(s)

    summary = _run(_purge())
    assert set(summary["deleted_projects"]) == {"acme/blog", "globex/portal"}
    assert summary["kept_projects"] == ["acme/web"]  # has a real domain → preserved
    assert summary["deleted_companies"] == ["globex"]  # fully empty → gone
    assert summary["kept_companies"] == ["acme"]  # still holds ACME Web

    companies, projects, domains = _run(_counts())
    assert companies == {"acme", "AD"}  # globex gone; adera untouched
    assert projects == {"acme/web", "AD/AD"}  # empty demo projects gone
    assert domains == 2  # no real domain deleted


def test_purge_is_idempotent(make_company, make_project):
    from scripts.purge_demo import purge_demo

    globex = make_company(code="globex", name="Globex")
    make_project(globex, code="portal", name="Globex Portal")

    async def _purge():
        async with SessionLocal() as s:
            return await purge_demo(s)

    first = _run(_purge())
    assert first["deleted_companies"] == ["globex"]
    # Second run finds nothing left to delete and does not error.
    second = _run(_purge())
    assert second == {
        "deleted_projects": [],
        "kept_projects": [],
        "deleted_companies": [],
        "kept_companies": [],
    }


def test_seed_guard_blocks_production(monkeypatch):
    from app.config import settings
    from scripts import seed

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.delenv("DG_ALLOW_SEED", raising=False)
    with pytest.raises(SystemExit):
        seed._guard_dev_only()

    monkeypatch.setenv("DG_ALLOW_SEED", "1")
    seed._guard_dev_only()  # override → no exit
