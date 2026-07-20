"""Write check results into the monthly-partitioned ``check_result`` table.

Each write first ensures the partition for that month exists (idempotent
CREATE TABLE IF NOT EXISTS), then inserts. Retention (dropping partitions older
than 12 months) is handled in T17.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.check_result import CheckResult


def _month_bounds(dt: datetime) -> tuple[str, str, str]:
    """Return (partition_suffix, from_date, to_date) for the month containing ``dt``."""
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    suffix = f"{start.year:04d}_{start.month:02d}"
    return suffix, start.date().isoformat(), end.date().isoformat()


async def ensure_partition(session: AsyncSession, dt: datetime) -> str:
    """Create the monthly partition for ``dt`` if missing; return its table name."""
    suffix, from_date, to_date = _month_bounds(dt)
    table = f"check_result_{suffix}"
    await session.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {table} PARTITION OF check_result "
            f"FOR VALUES FROM ('{from_date}') TO ('{to_date}')"
        )
    )
    return table


async def write_result(
    session: AsyncSession,
    *,
    domain_id: int,
    check_type: str,
    status: str,
    data: dict[str, Any] | None = None,
    checked_at: datetime | None = None,
) -> CheckResult:
    """Persist a check result (ensuring its month partition exists first)."""
    ts = checked_at or datetime.now(UTC)
    await ensure_partition(session, ts)
    result = CheckResult(
        domain_id=domain_id, type=check_type, status=status, data_json=data, checked_at=ts
    )
    session.add(result)
    await session.flush()
    return result
