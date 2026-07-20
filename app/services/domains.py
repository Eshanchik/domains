"""Domain registry service: CRUD, dedup, scoped listing, history, bulk, CSV."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.fqdn import normalize_fqdn
from app.models.company import Project, Tag
from app.models.domain import Domain, DomainFieldHistory, DomainTag
from app.models.user import Role, User
from app.schemas.domain import DomainCreate, DomainUpdate
from app.services import companies as companies_svc

# Fields whose changes are written to DomainFieldHistory (FR-DM-5).
TRACKED_FIELDS = (
    "expiry_date",
    "auto_renew",
    "nameservers",
    "epp_statuses",
    "registrant",
    "renewal_price",
    "project_id",
)


class DuplicateDomainError(Exception):
    """Raised when a domain with the same normalized FQDN already exists."""


@dataclass
class DomainFilter:
    company_id: int | None = None
    project_id: int | None = None
    tag: str | None = None
    registrar_id: int | None = None
    q: str | None = None
    expiring_days: int | None = None
    vt_detect: bool = False
    health_down: bool = False
    include_archived: bool = False
    sort: str = "fqdn"
    descending: bool = False
    page: int = 1
    page_size: int = 50


async def allowed_project_ids(session: AsyncSession, user: User) -> set[int] | None:
    """Project ids the user may see; ``None`` means unrestricted (admin)."""
    if user.role == Role.admin:
        return None
    projects = await companies_svc.list_projects(session, user)
    return {p.id for p in projects}


def _fmt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _apply_tags(session: AsyncSession, domain: Domain, names: list[str]) -> None:
    """Replace the domain's tags with the given names (creating tags as needed)."""
    tags: list[Tag] = []
    for raw in names:
        name = raw.strip()
        if name:
            tags.append(await companies_svc.get_or_create_tag(session, name))
    domain.tags = tags


async def get_domain(session: AsyncSession, domain_id: int) -> Domain | None:
    return await session.get(Domain, domain_id)


async def get_domain_with_history(session: AsyncSession, domain_id: int) -> Domain | None:
    """Load a domain with its field history eagerly (for the card, which renders it).

    Eager-loading here avoids a lazy load during template rendering, which happens
    after the request's async session closes (→ MissingGreenlet).
    """
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(Domain).options(selectinload(Domain.history)).where(Domain.id == domain_id)
    )
    return result.scalar_one_or_none()


async def get_by_fqdn(session: AsyncSession, fqdn: str) -> Domain | None:
    result = await session.execute(select(Domain).where(Domain.fqdn == fqdn))
    return result.scalar_one_or_none()


async def create_domain(session: AsyncSession, data: DomainCreate, *, actor_id: int) -> Domain:
    norm = normalize_fqdn(data.fqdn)
    if await get_by_fqdn(session, norm.fqdn) is not None:
        raise DuplicateDomainError(norm.fqdn)

    sources = {"fqdn": "manual", "project_id": "manual"}
    if data.expiry_date is not None:
        sources["expiry_date"] = "manual"

    domain = Domain(
        project_id=data.project_id,
        fqdn=norm.fqdn,
        punycode=norm.punycode,
        tld=norm.tld,
        expiry_date=data.expiry_date,
        renewal_price=data.renewal_price,
        renewal_currency=data.renewal_currency,
        renewal_period_months=data.renewal_period_months,
        ssl_extra_hosts=[h.strip() for h in data.ssl_extra_hosts if h.strip()],
        notes=data.notes,
        responsible_user_id=data.responsible_user_id,
        field_sources=sources,
    )
    await _apply_tags(session, domain, data.tags)
    session.add(domain)
    await session.flush()
    await record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="domain",
        entity_id=domain.id,
        diff={"fqdn": norm.fqdn, "project_id": data.project_id},
    )
    await session.commit()
    await session.refresh(domain)
    return domain


async def update_domain(
    session: AsyncSession,
    domain: Domain,
    data: DomainUpdate,
    *,
    actor_id: int,
    source: str = "manual",
) -> Domain:
    """Apply an update, writing DomainFieldHistory for tracked fields and marking
    field provenance. ``source`` defaults to manual (manual wins over autosync)."""
    changes: dict[str, Any] = data.model_dump(exclude_unset=True)
    tags = changes.pop("tags", None)
    field_sources = dict(domain.field_sources or {})

    for field, new_value in changes.items():
        old_value = getattr(domain, field)
        if old_value == new_value:
            continue
        if field in TRACKED_FIELDS:
            session.add(
                DomainFieldHistory(
                    domain_id=domain.id,
                    field=field,
                    old=_fmt(old_value),
                    new=_fmt(new_value),
                    source=source,
                )
            )
        setattr(domain, field, new_value)
        field_sources[field] = source

    if tags is not None:
        await _apply_tags(session, domain, tags)
        field_sources["tags"] = source

    domain.field_sources = field_sources
    await record_audit(
        session,
        actor_id=actor_id,
        action="update",
        entity_type="domain",
        entity_id=domain.id,
        diff={k: _fmt(v) for k, v in changes.items()},
    )
    await session.commit()
    await session.refresh(domain)
    return domain


async def set_archived(
    session: AsyncSession, domain: Domain, archived: bool, *, actor_id: int
) -> Domain:
    domain.is_active = not archived
    await record_audit(
        session,
        actor_id=actor_id,
        action="archive" if archived else "unarchive",
        entity_type="domain",
        entity_id=domain.id,
    )
    await session.commit()
    await session.refresh(domain)
    return domain


def _build_query(base: Select, flt: DomainFilter, allowed: set[int] | None) -> Select:
    conditions = []
    if allowed is not None:
        if not allowed:
            # No visible projects → match nothing.
            return base.where(Domain.id.is_(None))
        conditions.append(Domain.project_id.in_(allowed))
    if not flt.include_archived:
        conditions.append(Domain.is_active.is_(True))
    if flt.project_id is not None:
        conditions.append(Domain.project_id == flt.project_id)
    if flt.company_id is not None:
        conditions.append(
            Domain.project_id.in_(select(Project.id).where(Project.company_id == flt.company_id))
        )
    if flt.registrar_id is not None:
        conditions.append(Domain.registrar_id == flt.registrar_id)
    if flt.q:
        like = f"%{flt.q.strip().lower()}%"
        conditions.append(or_(Domain.fqdn.ilike(like), Domain.punycode.ilike(like)))
    if flt.tag:
        conditions.append(
            Domain.id.in_(
                select(DomainTag.domain_id)
                .join(Tag, Tag.id == DomainTag.tag_id)
                .where(Tag.name == flt.tag)
            )
        )
    if flt.expiring_days is not None:
        cutoff = datetime.now(UTC) + timedelta(days=flt.expiring_days)
        conditions.append(and_(Domain.expiry_date.is_not(None), Domain.expiry_date <= cutoff))
    if flt.vt_detect:
        from app.models.alert import AlertEvent

        conditions.append(
            Domain.id.in_(
                select(AlertEvent.domain_id).where(
                    AlertEvent.kind == "vt_malicious", AlertEvent.state == "active"
                )
            )
        )
    if flt.health_down:
        from app.models.healthcheck import HealthCheck

        conditions.append(
            Domain.id.in_(select(HealthCheck.domain_id).where(HealthCheck.state == "down"))
        )
    if conditions:
        base = base.where(and_(*conditions))
    return base


async def list_domains(
    session: AsyncSession, user: User, flt: DomainFilter
) -> tuple[list[Domain], int]:
    """Return (page_items, total_count) honoring scope, filters, sort, pagination."""
    allowed = await allowed_project_ids(session, user)

    count_stmt = _build_query(select(func.count()).select_from(Domain), flt, allowed)
    total = (await session.execute(count_stmt)).scalar_one()

    sort_col = {
        "fqdn": Domain.fqdn,
        "expiry_date": Domain.expiry_date,
        "tld": Domain.tld,
        "created_at": Domain.created_at,
    }.get(flt.sort, Domain.fqdn)
    order = sort_col.desc() if flt.descending else sort_col.asc()

    stmt = _build_query(select(Domain), flt, allowed).order_by(order)
    page = max(1, flt.page)
    stmt = stmt.limit(flt.page_size).offset((page - 1) * flt.page_size)
    items = list((await session.execute(stmt)).scalars().all())
    return items, total


async def bulk_assign_project(
    session: AsyncSession, user: User, ids: list[int], project_id: int, *, actor_id: int
) -> int:
    domains = await _load_scoped(session, user, ids)
    for d in domains:
        if d.project_id != project_id:
            session.add(
                DomainFieldHistory(
                    domain_id=d.id,
                    field="project_id",
                    old=str(d.project_id),
                    new=str(project_id),
                    source="manual",
                )
            )
            d.project_id = project_id
    await record_audit(
        session,
        actor_id=actor_id,
        action="bulk_assign_project",
        entity_type="domain",
        entity_id=None,
        diff={"ids": ids, "project_id": project_id},
    )
    await session.commit()
    return len(domains)


async def bulk_add_tags(
    session: AsyncSession, user: User, ids: list[int], tag_names: list[str], *, actor_id: int
) -> int:
    domains = await _load_scoped(session, user, ids)
    new_tags = [
        await companies_svc.get_or_create_tag(session, n.strip()) for n in tag_names if n.strip()
    ]
    for d in domains:
        existing = {t.id for t in d.tags}
        d.tags = d.tags + [t for t in new_tags if t.id not in existing]
    await record_audit(
        session,
        actor_id=actor_id,
        action="bulk_add_tags",
        entity_type="domain",
        entity_id=None,
        diff={"ids": ids, "tags": tag_names},
    )
    await session.commit()
    return len(domains)


async def bulk_archive(
    session: AsyncSession, user: User, ids: list[int], archived: bool, *, actor_id: int
) -> int:
    domains = await _load_scoped(session, user, ids)
    for d in domains:
        d.is_active = not archived
    await record_audit(
        session,
        actor_id=actor_id,
        action="bulk_archive",
        entity_type="domain",
        entity_id=None,
        diff={"ids": ids, "archived": archived},
    )
    await session.commit()
    return len(domains)


async def _load_scoped(session: AsyncSession, user: User, ids: list[int]) -> list[Domain]:
    """Load domains by id, restricted to the user's allowed projects (skip others)."""
    if not ids:
        return []
    allowed = await allowed_project_ids(session, user)
    stmt = select(Domain).where(Domain.id.in_(ids))
    if allowed is not None:
        if not allowed:
            return []
        stmt = stmt.where(Domain.project_id.in_(allowed))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def export_csv(session: AsyncSession, user: User, flt: DomainFilter) -> str:
    """Export the scoped/filtered domain list as CSV text (FR-DM-4)."""
    flt.page = 1
    flt.page_size = 100_000  # export is unpaginated
    items, _ = await list_domains(session, user, flt)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "fqdn",
            "tld",
            "project_id",
            "expiry_date",
            "auto_renew",
            "is_active",
            "renewal_price",
            "renewal_currency",
            "tags",
        ]
    )
    for d in items:
        writer.writerow(
            [
                d.fqdn,
                d.tld,
                d.project_id,
                d.expiry_date.isoformat() if d.expiry_date else "",
                "" if d.auto_renew is None else d.auto_renew,
                d.is_active,
                d.renewal_price if d.renewal_price is not None else "",
                d.renewal_currency,
                ",".join(t.name for t in d.tags),
            ]
        )
    return buf.getvalue()
