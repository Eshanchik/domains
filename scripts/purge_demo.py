"""Remove seeded demo data (ACME/Globex) safely and idempotently.

The demo companies come from ``scripts/seed.py`` (codes ``acme`` / ``globex``).
This purges them and their projects from a database WITHOUT touching real data:
a project is deleted only when it has **zero domains**, and a demo company is
deleted only once it has **no projects left**. Any demo project that has since
received a real domain (e.g. a domain manually assigned to "ACME Web") is kept,
and so is its company — the script reports these as skipped.

Run: ``python -m scripts.purge_demo``  (idempotent — safe to run repeatedly)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.log import configure_logging
from app.models.company import Company, Project
from app.models.domain import Domain

log = logging.getLogger("purge_demo")

# Demo companies created by scripts/seed.py, matched by (code, name) so a real
# company that merely reuses one of these codes is never touched.
DEMO_COMPANIES = (("acme", "ACME Corp"), ("globex", "Globex"))


async def purge_demo(session: AsyncSession) -> dict[str, Any]:
    """Delete empty demo projects/companies. Returns a summary of what happened."""
    deleted_projects: list[str] = []
    kept_projects: list[str] = []
    deleted_companies: list[str] = []
    kept_companies: list[str] = []

    for code, name in DEMO_COMPANIES:
        company = (
            await session.execute(select(Company).where(Company.code == code, Company.name == name))
        ).scalar_one_or_none()
        if company is None:
            continue  # already purged (or never seeded)

        projects = list(
            (
                await session.execute(select(Project).where(Project.company_id == company.id))
            ).scalars()
        )
        for project in projects:
            domain_count = (
                await session.execute(
                    select(func.count()).select_from(Domain).where(Domain.project_id == project.id)
                )
            ).scalar_one()
            if domain_count == 0:
                log.info("deleting empty demo project %s/%s", code, project.code)
                # Core delete (not ORM) so no relationship cascade fires.
                await session.execute(delete(Project).where(Project.id == project.id))
                deleted_projects.append(f"{code}/{project.code}")
            else:
                log.info(
                    "keeping demo project %s/%s (has %d domain(s))",
                    code,
                    project.code,
                    domain_count,
                )
                kept_projects.append(f"{code}/{project.code}")

        remaining = (
            await session.execute(
                select(func.count()).select_from(Project).where(Project.company_id == company.id)
            )
        ).scalar_one()
        if remaining == 0:
            log.info("deleting empty demo company %s", code)
            await session.execute(delete(Company).where(Company.id == company.id))
            deleted_companies.append(code)
        else:
            log.info("keeping demo company %s (%d project(s) still hold data)", code, remaining)
            kept_companies.append(code)

    await session.commit()
    return {
        "deleted_projects": deleted_projects,
        "kept_projects": kept_projects,
        "deleted_companies": deleted_companies,
        "kept_companies": kept_companies,
    }


async def _run() -> None:
    async with SessionLocal() as session:
        summary = await purge_demo(session)
    log.info(
        "purge complete: %d project(s) deleted, %d kept; %d company(ies) deleted, %d kept",
        len(summary["deleted_projects"]),
        len(summary["kept_projects"]),
        len(summary["deleted_companies"]),
        len(summary["kept_companies"]),
    )


def main() -> None:
    configure_logging()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
