"""Dashboard aggregates (SPEC FR-UI-1), scoped to the user.

All counters are single aggregate queries over indexed columns so the overview stays
under ~1s even at 10k domains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertEvent
from app.models.company import Company, Project
from app.models.domain import Domain
from app.models.healthcheck import HealthCheck
from app.models.user import User
from app.services import domains as domains_svc


@dataclass
class CompanyRow:
    name: str
    domains: int
    expiring_30: int
    ssl_problems: int = 0
    cost_usd: float = 0.0


@dataclass
class Overview:
    total: int = 0
    expiring_7: int = 0
    expiring_30: int = 0
    expiring_90: int = 0
    ssl_problems: int = 0
    vt_detects: int = 0
    health_down: int = 0
    by_company: list[CompanyRow] = field(default_factory=list)


async def _count(session, stmt) -> int:
    return (await session.execute(stmt)).scalar_one() or 0


async def build_overview(
    session: AsyncSession, user: User, *, now: datetime | None = None
) -> Overview:
    ts = now or datetime.now(UTC)
    allowed = await domains_svc.allowed_project_ids(session, user)

    def scoped(stmt):
        if allowed is None:
            return stmt
        if not allowed:
            return stmt.where(Domain.id.is_(None))
        return stmt.where(Domain.project_id.in_(allowed))

    active = Domain.is_active.is_(True)

    def expiring_count(days: int):
        cutoff = ts + timedelta(days=days)
        return scoped(
            select(func.count())
            .select_from(Domain)
            .where(active, Domain.expiry_date.is_not(None), Domain.expiry_date <= cutoff)
        )

    ov = Overview(
        total=await _count(session, scoped(select(func.count()).select_from(Domain).where(active))),
        expiring_7=await _count(session, expiring_count(7)),
        expiring_30=await _count(session, expiring_count(30)),
        expiring_90=await _count(session, expiring_count(90)),
    )

    def active_alert_domains(kind: str):
        return scoped(
            select(func.count(func.distinct(Domain.id)))
            .select_from(Domain)
            .join(AlertEvent, AlertEvent.domain_id == Domain.id)
            .where(active, AlertEvent.state == "active", AlertEvent.kind == kind)
        )

    ov.ssl_problems = await _count(session, active_alert_domains("ssl"))
    ov.vt_detects = await _count(session, active_alert_domains("vt_malicious"))
    ov.health_down = await _count(
        session,
        scoped(
            select(func.count(func.distinct(Domain.id)))
            .select_from(Domain)
            .join(HealthCheck, HealthCheck.domain_id == Domain.id)
            .where(active, HealthCheck.state == "down")
        ),
    )

    # Per-company breakdown (domains + expiring within 30 days).
    cutoff_30 = ts + timedelta(days=30)
    ov.by_company = await _company_breakdown(session, scoped, active, cutoff_30)
    return ov


async def _company_breakdown(session, scoped, active, cutoff_30) -> list[CompanyRow]:
    total_rows = (
        await session.execute(
            scoped(
                select(Company.name, func.count(Domain.id))
                .select_from(Domain)
                .join(Project, Project.id == Domain.project_id)
                .join(Company, Company.id == Project.company_id)
                .where(active)
                .group_by(Company.name)
                .order_by(Company.name)
            )
        )
    ).all()
    exp_rows = dict(
        (
            await session.execute(
                scoped(
                    select(Company.name, func.count(Domain.id))
                    .select_from(Domain)
                    .join(Project, Project.id == Domain.project_id)
                    .join(Company, Company.id == Project.company_id)
                    .where(active, Domain.expiry_date.is_not(None), Domain.expiry_date <= cutoff_30)
                    .group_by(Company.name)
                )
            )
        ).all()
    )
    # SSL-fail count per company (domains with an active ssl alert).
    ssl_rows = dict(
        (
            await session.execute(
                scoped(
                    select(Company.name, func.count(func.distinct(Domain.id)))
                    .select_from(Domain)
                    .join(Project, Project.id == Domain.project_id)
                    .join(Company, Company.id == Project.company_id)
                    .join(AlertEvent, AlertEvent.domain_id == Domain.id)
                    .where(active, AlertEvent.state == "active", AlertEvent.kind == "ssl")
                    .group_by(Company.name)
                )
            )
        ).all()
    )
    # Annual renewal cost per company (USD-priced domains only).
    cost_rows = dict(
        (
            await session.execute(
                scoped(
                    select(Company.name, func.coalesce(func.sum(Domain.renewal_price), 0))
                    .select_from(Domain)
                    .join(Project, Project.id == Domain.project_id)
                    .join(Company, Company.id == Project.company_id)
                    .where(
                        active,
                        Domain.renewal_price.is_not(None),
                        Domain.renewal_currency == "USD",
                    )
                    .group_by(Company.name)
                )
            )
        ).all()
    )
    return [
        CompanyRow(
            name=name,
            domains=cnt,
            expiring_30=exp_rows.get(name, 0),
            ssl_problems=ssl_rows.get(name, 0),
            cost_usd=float(cost_rows.get(name, 0) or 0),
        )
        for name, cnt in total_rows
    ]
