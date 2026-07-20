"""Active alerts page + alert detail (SPEC FR-UI-4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_role, require_user
from app.models.alert import AlertEvent
from app.models.check_result import CheckResult
from app.models.domain import Domain
from app.models.user import Role, User
from app.services import alerts as alerts_svc
from app.services import domains as domains_svc
from app.templating import templates

router = APIRouter(tags=["web-alerts"])
manager_required = require_role(Role.manager)


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    allowed = await domains_svc.allowed_project_ids(session, user)
    stmt = (
        select(AlertEvent, Domain.fqdn)
        .join(Domain, Domain.id == AlertEvent.domain_id)
        .where(AlertEvent.state == "active")
        .order_by(AlertEvent.fired_at.desc())
    )
    if allowed is not None:
        stmt = (
            stmt.where(Domain.project_id.in_(allowed))
            if allowed
            else stmt.where(Domain.id.is_(None))
        )
    rows = (await session.execute(stmt)).all()
    return templates.TemplateResponse(request, "alerts/list.html", {"user": user, "rows": rows})


async def _load_alert_in_scope(
    session: AsyncSession, user: User, alert_id: int
) -> tuple[AlertEvent, Domain] | None:
    """Fetch an alert + its domain, or None if missing / out of the user's scope."""
    event = await session.get(AlertEvent, alert_id)
    if event is None:
        return None
    domain = await session.get(Domain, event.domain_id)
    if domain is None:
        return None
    allowed = await domains_svc.allowed_project_ids(session, user)
    if allowed is not None and domain.project_id not in allowed:
        return None
    return event, domain


@router.get("/alerts/{alert_id}", response_class=HTMLResponse)
async def alert_detail(
    request: Request,
    alert_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    found = await _load_alert_in_scope(session, user, alert_id)
    if found is None:
        return RedirectResponse("/alerts", status_code=status.HTTP_303_SEE_OTHER)
    event, domain = found
    recent_checks = list(
        (
            await session.execute(
                select(CheckResult)
                .where(CheckResult.domain_id == domain.id)
                .order_by(CheckResult.checked_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "alerts/detail.html",
        {"user": user, "event": event, "domain": domain, "recent_checks": recent_checks},
    )


@router.post("/alerts/{alert_id}/resolve")
async def alert_resolve(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    found = await _load_alert_in_scope(session, user, alert_id)
    if found is not None:
        await alerts_svc.resolve_event(session, alert_id)
    return RedirectResponse(f"/alerts/{alert_id}", status_code=status.HTTP_303_SEE_OTHER)
