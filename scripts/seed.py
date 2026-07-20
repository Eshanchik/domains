"""Seed demo data: 2 companies and 5 projects (idempotent, upsert by code).

DEV ONLY — this inserts fake ACME/Globex data and must never run against a
production database. It is not part of the deploy path; the guard below refuses
to run when ENVIRONMENT=production unless DG_ALLOW_SEED=1 is set explicitly.

Run: ``python -m scripts.seed``
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.log import configure_logging
from app.models.company import Company, Project

log = logging.getLogger("seed")


def _guard_dev_only() -> None:
    """Refuse to seed a production database unless explicitly overridden."""
    if settings.environment == "production" and os.getenv("DG_ALLOW_SEED") != "1":
        log.error("refusing to seed production data (set DG_ALLOW_SEED=1 to override)")
        sys.exit(2)


COMPANIES = [
    {"code": "acme", "name": "ACME Corp"},
    {"code": "globex", "name": "Globex"},
]

PROJECTS = [
    {"company": "acme", "code": "web", "name": "ACME Web"},
    {"company": "acme", "code": "shop", "name": "ACME Shop"},
    {"company": "acme", "code": "blog", "name": "ACME Blog"},
    {"company": "globex", "code": "portal", "name": "Globex Portal"},
    {"company": "globex", "code": "api", "name": "Globex API"},
]


async def _run() -> None:
    async with SessionLocal() as session:
        companies: dict[str, Company] = {}
        for row in COMPANIES:
            existing = (
                await session.execute(select(Company).where(Company.code == row["code"]))
            ).scalar_one_or_none()
            if existing is None:
                existing = Company(name=row["name"], code=row["code"])
                session.add(existing)
                await session.flush()
                log.info("company created: %s", row["code"])
            companies[row["code"]] = existing

        for row in PROJECTS:
            company = companies[row["company"]]
            existing = (
                await session.execute(
                    select(Project).where(
                        Project.company_id == company.id, Project.code == row["code"]
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(Project(company_id=company.id, name=row["name"], code=row["code"]))
                log.info("project created: %s/%s", row["company"], row["code"])

        await session.commit()
    log.info("seed complete: %d companies, %d projects", len(COMPANIES), len(PROJECTS))


def main() -> None:
    configure_logging()
    _guard_dev_only()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
