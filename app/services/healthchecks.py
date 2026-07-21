"""Health-check CRUD and template bulk-add (SPEC FR-CK-4)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import net_guard
from app.core.audit import record_audit
from app.models.domain import Domain
from app.models.healthcheck import HealthCheck
from app.schemas.healthcheck import HealthCheckCreate


class InvalidHealthCheckUrl(ValueError):
    """The health-check URL is not an allowed http(s) target."""


async def list_for_domain(session: AsyncSession, domain_id: int) -> list[HealthCheck]:
    result = await session.execute(
        select(HealthCheck).where(HealthCheck.domain_id == domain_id).order_by(HealthCheck.id)
    )
    return list(result.scalars().all())


async def get(session: AsyncSession, healthcheck_id: int) -> HealthCheck | None:
    return await session.get(HealthCheck, healthcheck_id)


async def create(
    session: AsyncSession, domain_id: int, data: HealthCheckCreate, *, actor_id: int
) -> HealthCheck:
    try:
        net_guard.validate_scheme(data.url)
    except net_guard.UnsafeUrlError as exc:
        raise InvalidHealthCheckUrl(str(exc)) from exc
    hc = HealthCheck(
        domain_id=domain_id,
        url=data.url,
        method=data.method.upper(),
        follow_redirects=data.follow_redirects,
        expected_statuses=data.expected_statuses,
        location_pattern=data.location_pattern or None,
        body_substring=data.body_substring or None,
        timeout_s=data.timeout_s,
        interval_min=data.interval_min,
        fail_threshold=data.fail_threshold,
        next_check_at=datetime.now(UTC),
    )
    session.add(hc)
    await session.flush()
    await record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="healthcheck",
        entity_id=hc.id,
        diff={"domain_id": domain_id, "url": data.url},
    )
    await session.commit()
    await session.refresh(hc)
    return hc


async def delete(session: AsyncSession, hc: HealthCheck, *, actor_id: int) -> None:
    await record_audit(
        session,
        actor_id=actor_id,
        action="delete",
        entity_type="healthcheck",
        entity_id=hc.id,
        diff={"url": hc.url},
    )
    await session.delete(hc)
    await session.commit()


async def bulk_add_template(
    session: AsyncSession, domain_ids: list[int], template: HealthCheckCreate, *, actor_id: int
) -> int:
    """Apply a health-check template to many domains, substituting ``{fqdn}`` in the URL."""
    if not domain_ids:
        return 0
    rows = await session.execute(select(Domain).where(Domain.id.in_(domain_ids)))
    domains = list(rows.scalars().all())
    now = datetime.now(UTC)
    for domain in domains:
        url = template.url.replace("{fqdn}", domain.fqdn)
        try:
            net_guard.validate_scheme(url)
        except net_guard.UnsafeUrlError as exc:
            raise InvalidHealthCheckUrl(str(exc)) from exc
        session.add(
            HealthCheck(
                domain_id=domain.id,
                url=url,
                method=template.method.upper(),
                follow_redirects=template.follow_redirects,
                expected_statuses=template.expected_statuses,
                location_pattern=template.location_pattern or None,
                body_substring=template.body_substring or None,
                timeout_s=template.timeout_s,
                interval_min=template.interval_min,
                fail_threshold=template.fail_threshold,
                next_check_at=now,
            )
        )
    await record_audit(
        session,
        actor_id=actor_id,
        action="bulk_add",
        entity_type="healthcheck",
        entity_id=None,
        diff={"domains": len(domains), "url_template": template.url},
    )
    await session.commit()
    return len(domains)
