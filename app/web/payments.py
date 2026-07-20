"""Payment recording (domain card) + cost summary page (SPEC FR-CT)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import redis_dep, require_role, require_user
from app.models.user import Role, User
from app.services import domains as domains_svc
from app.services import payments as svc
from app.templating import templates

router = APIRouter(tags=["web-payments"])
manager_required = require_role(Role.manager)


@router.post("/domains/{domain_id}/payments")
async def add_payment(
    domain_id: int,
    amount: str = Form(...),
    currency: str = Form("USD"),
    paid_at: str = Form(""),
    rate_override: str = Form(""),
    note: str = Form(""),
    session: AsyncSession = Depends(get_session),
    redis=Depends(redis_dep),
    user: User = Depends(manager_required),
):
    domain = await domains_svc.get_domain(session, domain_id)
    allowed = await domains_svc.allowed_project_ids(session, user)
    if domain is None or (allowed is not None and domain.project_id not in allowed):
        return RedirectResponse("/domains", status_code=status.HTTP_303_SEE_OTHER)
    try:
        amt = Decimal(amount)
        override = Decimal(rate_override) if rate_override.strip() else None
        when = datetime.fromisoformat(paid_at) if paid_at.strip() else datetime.now(UTC)
        await svc.add_payment(
            session,
            redis,
            domain_id=domain_id,
            amount=amt,
            currency=currency,
            paid_at=when,
            note=note or None,
            rate_override=override,
            actor_id=user.id,
        )
    except (InvalidOperation, ValueError):
        return RedirectResponse(f"/domains/{domain_id}?pay=badinput", status_code=303)
    except svc.RateUnavailableError:
        return RedirectResponse(f"/domains/{domain_id}?pay=norate", status_code=303)
    return RedirectResponse(f"/domains/{domain_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/costs", response_class=HTMLResponse)
async def costs_page(
    request: Request,
    group_by: str = Query("company"),
    year: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    now = datetime.now(UTC)
    y = year or now.year
    start = datetime(y, 1, 1, tzinfo=UTC)
    end = datetime(y + 1, 1, 1, tzinfo=UTC)
    group_by = group_by if group_by in {"company", "project", "registrar"} else "company"

    summary = await svc.cost_summary(session, user, start=start, end=end, group_by=group_by)
    forecast = await svc.upcoming_renewals(session, user, days=30, now=now)
    total = sum((r.total_usd for r in summary), start=Decimal(0))
    return templates.TemplateResponse(
        request,
        "costs/page.html",
        {
            "user": user,
            "summary": summary,
            "forecast": forecast,
            "total": total,
            "group_by": group_by,
            "year": y,
        },
    )
