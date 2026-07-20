"""Seed demo data: 2 companies and 5 projects (idempotent, upsert by code).

Run: ``python -m scripts.seed``
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.db import SessionLocal
from app.log import configure_logging
from app.models.company import Company, Project

log = logging.getLogger("seed")

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
    asyncio.run(_run())


if __name__ == "__main__":
    main()
