"""Generate N synthetic domains for performance testing (SPEC NFR-2, T14 acceptance).

Spreads expiry dates so some fall in the 7/30/90-day windows. Uses a batched core
insert for speed.

Run: ``python -m scripts.seed_bulk 10000``
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, select

from app.db import SessionLocal
from app.log import configure_logging
from app.models.company import Project
from app.models.domain import Domain

log = logging.getLogger("seed_bulk")


async def _run(total: int) -> None:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        project_ids = list((await session.execute(select(Project.id))).scalars().all())
        if not project_ids:
            log.error("no projects — run `python -m scripts.seed` first")
            return

        batch: list[dict] = []
        created = 0
        for i in range(total):
            fqdn = f"perf-{i}.example"
            batch.append(
                {
                    "project_id": project_ids[i % len(project_ids)],
                    "fqdn": fqdn,
                    "punycode": fqdn,
                    "tld": "example",
                    "expiry_date": now + timedelta(days=(i % 400) - 50),
                    "epp_statuses": [],
                    "nameservers": [],
                    "ssl_extra_hosts": [],
                    "field_sources": {},
                    "renewal_currency": "USD",
                    "renewal_period_months": 12,
                    "is_active": True,
                }
            )
            if len(batch) >= 1000:
                await session.execute(insert(Domain), batch)
                await session.commit()
                created += len(batch)
                batch = []
        if batch:
            await session.execute(insert(Domain), batch)
            await session.commit()
            created += len(batch)
    log.info("seed_bulk: created %d domains", created)


def main() -> None:
    configure_logging()
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    asyncio.run(_run(total))


if __name__ == "__main__":
    main()
