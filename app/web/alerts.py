"""Active alerts page + alert detail (SPEC FR-UI-4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import redis_dep, require_role, require_user
from app.models.alert import AlertEvent
from app.models.check_result import CheckResult
from app.models.company import Company, Project
from app.models.domain import Domain
from app.models.user import Role, User
from app.services import alerts as alerts_svc
from app.services import domains as domains_svc
from app.services import notifications as notif
from app.templating import templates

router = APIRouter(tags=["web-alerts"])
manager_required = require_role(Role.manager)


def format_age(delta: timedelta) -> str:
    """Compact age string for how long an alert has been active: «3д 4ч» / «5ч 12м» / «8м»."""
    total = max(0, int(delta.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}д {hours}ч"
    if hours:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    allowed = await domains_svc.allowed_project_ids(session, user)
    stmt = (
        select(AlertEvent, Domain.fqdn, Project.name, Company.name)
        .join(Domain, Domain.id == AlertEvent.domain_id)
        .join(Project, Project.id == Domain.project_id)
        .join(Company, Company.id == Project.company_id)
        .where(AlertEvent.state == "active")
        .order_by(AlertEvent.fired_at.desc())
    )
    if allowed is not None:
        stmt = (
            stmt.where(Domain.project_id.in_(allowed))
            if allowed
            else stmt.where(Domain.id.is_(None))
        )
    raw = (await session.execute(stmt)).all()
    now = datetime.now(UTC)
    rows = [
        {
            "event": event,
            "fqdn": fqdn,
            "project": project,
            "company": company,
            "age": format_age(now - event.fired_at),
        }
        for event, fqdn, project, company in raw
    ]
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
    notified: str | None = None,
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
        {
            "user": user,
            "event": event,
            "domain": domain,
            "recent_checks": recent_checks,
            "notified": notified,
        },
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


@router.post("/alerts/{alert_id}/notify")
async def alert_notify(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
    redis=Depends(redis_dep),
    user: User = Depends(manager_required),
):
    """Re-send this alert to the domain's resolved notification channels (Manager+)."""
    found = await _load_alert_in_scope(session, user, alert_id)
    sent = 0
    if found is not None:
        event, domain = found
        text = alerts_svc.build_message(event, domain)
        for channel in await notif.resolve_channels(session, domain, purpose="instant"):
            if await notif.send_to_channel(session, redis, channel, text):
                sent += 1
    return RedirectResponse(
        f"/alerts/{alert_id}?notified={sent}", status_code=status.HTTP_303_SEE_OTHER
    )
