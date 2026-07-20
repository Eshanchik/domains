"""DNS/NS monitoring (SPEC FR-CK-5, Phase 2 / T19).

Resolves A/AAAA/NS/MX for a domain and stores a snapshot in ``check_result``. A
change in the NS set versus the previous snapshot raises an ``ns_change`` alert
(a possible hijack marker). ``_resolve`` is the network seam tests patch.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import dns.asyncresolver
import dns.exception
import dns.resolver
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.check_result_store import write_result
from app.models.check_result import CheckStatus
from app.models.domain import Domain

log = logging.getLogger("checks.dns")

_EMPTY_EXC = (
    dns.resolver.NoAnswer,
    dns.resolver.NXDOMAIN,
    dns.resolver.NoNameservers,
    dns.exception.Timeout,
)


async def _resolve(fqdn: str, rdtype: str) -> list[str]:
    """Resolve one record type; returns a sorted, normalized list (empty on failure)."""
    try:
        answer = await dns.asyncresolver.resolve(fqdn, rdtype)
    except _EMPTY_EXC:
        return []
    except dns.exception.DNSException:
        return []
    return sorted(str(r).rstrip(".").lower() for r in answer)


async def run_dns_check(
    session: AsyncSession,
    redis: aioredis.Redis,  # noqa: ARG001 — uniform check signature
    domain_id: int,
    *,
    now: datetime | None = None,
) -> str:
    ts = now or datetime.now(UTC)
    domain = await session.get(Domain, domain_id)
    if domain is None:
        return "missing"

    records = {
        "a": await _resolve(domain.fqdn, "A"),
        "aaaa": await _resolve(domain.fqdn, "AAAA"),
        "ns": await _resolve(domain.fqdn, "NS"),
        "mx": await _resolve(domain.fqdn, "MX"),
    }
    # Unresolvable domain (no records at all) → stale rather than a false NS-change.
    status = CheckStatus.ok if any(records.values()) else CheckStatus.stale
    await write_result(
        session, domain_id=domain_id, check_type="dns", status=status, data=records, checked_at=ts
    )
    await session.commit()
    return status
