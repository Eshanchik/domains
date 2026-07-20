"""Active alerts page (SPEC FR-UI-4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_user
from app.models.alert import AlertEvent
from app.models.domain import Domain
from app.models.user import User
from app.services import domains as domains_svc
from app.templating import templates

router = APIRouter(tags=["web-alerts"])


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
