"""Payments, cost summaries, and renewal forecast (SPEC FR-CT-1..4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.models.company import Company, Project
from app.models.domain import Domain
from app.models.payment import Payment
from app.models.user import User
from app.services import domains as domains_svc
from app.services import rates as rates_svc


class RateUnavailableError(Exception):
    """Raised when a non-USD payment has no rate (API down and no manual override)."""


async def list_for_domain(session: AsyncSession, domain_id: int) -> list[Payment]:
    result = await session.execute(
        select(Payment).where(Payment.domain_id == domain_id).order_by(Payment.paid_at.desc())
    )
    return list(result.scalars().all())


async def add_payment(
    session: AsyncSession,
    redis: aioredis.Redis,
    *,
    domain_id: int,
    amount: Decimal,
    currency: str,
    paid_at: datetime | None = None,
    note: str | None = None,
    rate_override: Decimal | None = None,
    actor_id: int,
) -> Payment:
    ts = paid_at or datetime.now(UTC)
    currency = currency.upper()[:3]

    if currency == "USD":
        rate = Decimal(1)
    elif rate_override is not None:
        rate = rate_override
    else:
        rate = await rates_svc.get_rate_to_usd(redis, currency, day=ts.strftime("%Y%m%d"))
        if rate is None:
            raise RateUnavailableError(currency)

    amount_usd = (amount * rate).quantize(Decimal("0.01"))
    payment = Payment(
        domain_id=domain_id,
        paid_at=ts,
        amount=amount,
        currency=currency,
        rate_to_usd=rate,
        amount_usd=amount_usd,
        note=note,
    )
    session.add(payment)
    await session.flush()
    await record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="payment",
        entity_id=payment.id,
        diff={"domain_id": domain_id, "amount_usd": str(amount_usd)},
    )
    await session.commit()
    await session.refresh(payment)
    return payment


@dataclass
class SummaryRow:
    label: str
    total_usd: Decimal


async def cost_summary(
    session: AsyncSession,
    user: User,
    *,
    start: datetime,
    end: datetime,
    group_by: str = "company",
) -> list[SummaryRow]:
    """Sum payment amount_usd over [start, end), grouped by company/project/registrar."""
    allowed = await domains_svc.allowed_project_ids(session, user)

    label_col = {
        "company": Company.name,
        "project": Project.name,
        "registrar": Domain.registrar_id,
    }.get(group_by, Company.name)

    stmt = (
        select(label_col, func.coalesce(func.sum(Payment.amount_usd), 0))
        .select_from(Payment)
        .join(Domain, Domain.id == Payment.domain_id)
        .join(Project, Project.id == Domain.project_id)
        .join(Company, Company.id == Project.company_id)
        .where(Payment.paid_at >= start, Payment.paid_at < end)
        .group_by(label_col)
        .order_by(label_col)
    )
    if allowed is not None:
        if not allowed:
            return []
        stmt = stmt.where(Domain.project_id.in_(allowed))

    rows = (await session.execute(stmt)).all()
    return [SummaryRow(label=str(label), total_usd=Decimal(total)) for label, total in rows]


@dataclass
class ForecastItem:
    fqdn: str
    expiry_date: datetime
    price: Decimal | None
    currency: str


async def upcoming_renewals(
    session: AsyncSession, user: User, *, days: int = 30, now: datetime | None = None
) -> list[ForecastItem]:
    """Domains expiring within ``days`` that have a renewal price (SPEC FR-CT-4)."""
    ts = now or datetime.now(UTC)
    allowed = await domains_svc.allowed_project_ids(session, user)
    stmt = (
        select(Domain.fqdn, Domain.expiry_date, Domain.renewal_price, Domain.renewal_currency)
        .where(
            Domain.is_active.is_(True),
            Domain.expiry_date.is_not(None),
            Domain.expiry_date <= ts + timedelta(days=days),
            Domain.renewal_price.is_not(None),
        )
        .order_by(Domain.expiry_date)
    )
    if allowed is not None:
        if not allowed:
            return []
        stmt = stmt.where(Domain.project_id.in_(allowed))
    rows = (await session.execute(stmt)).all()
    return [
        ForecastItem(fqdn=f, expiry_date=e, price=p, currency=c or "USD") for f, e, p, c in rows
    ]
