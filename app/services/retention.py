"""History retention: drop check_result partitions and prune old rows (SPEC NFR-7).

Keeps 12 months. ``check_result`` is monthly-partitioned, so old data is dropped by
dropping whole partitions (cheap); ``health_check_results`` is a regular table pruned
by a dated DELETE.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("services.retention")

RETENTION_MONTHS = 12
_PART_RE = re.compile(r"^check_result_(\d{4})_(\d{2})$")


def _cutoff(now: datetime) -> datetime:
    """First day of the month 12 months before ``now`` (partitions older are dropped)."""
    total = now.year * 12 + (now.month - 1) - RETENTION_MONTHS
    return datetime(total // 12, total % 12 + 1, 1, tzinfo=UTC)


async def drop_old_partitions(session: AsyncSession, *, now: datetime | None = None) -> list[str]:
    """Drop check_result monthly partitions older than the retention window."""
    ts = now or datetime.now(UTC)
    cutoff = _cutoff(ts)
    rows = await session.execute(
        text(
            "SELECT inhrelid::regclass::text FROM pg_inherits "
            "WHERE inhparent = 'check_result'::regclass"
        )
    )
    dropped: list[str] = []
    for (name,) in rows.all():
        m = _PART_RE.match(name)
        if not m:
            continue
        part_month = datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=UTC)
        if part_month < cutoff:
            await session.execute(text(f"DROP TABLE IF EXISTS {name}"))
            dropped.append(name)
    if dropped:
        await session.commit()
        log.info("retention: dropped partitions %s", dropped)
    return dropped


async def prune_health_results(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Delete health_check_results older than the retention window."""
    ts = now or datetime.now(UTC)
    cutoff = _cutoff(ts)
    result = await session.execute(
        text("DELETE FROM health_check_results WHERE checked_at < :cutoff"),
        {"cutoff": cutoff},
    )
    await session.commit()
    return result.rowcount or 0


async def run_retention(session: AsyncSession, *, now: datetime | None = None) -> dict:
    dropped = await drop_old_partitions(session, now=now)
    pruned = await prune_health_results(session, now=now)
    return {"dropped_partitions": dropped, "pruned_health_results": pruned}
