"""REST API v1 (token-authenticated, scoped) — SPEC FR-API-1.

Same services as the web UI (no duplicated logic). Auth is a personal API token via
``Authorization: Bearer <token>``; results are filtered by the token owner's scope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import api_user
from app.models.alert import AlertEvent
from app.models.domain import Domain
from app.models.user import User
from app.schemas.domain import DomainRead
from app.services import domains as domains_svc
from app.services.domains import DomainFilter

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


@router.get("/me")
async def me(user: User = Depends(api_user)) -> dict:
    return {"id": user.id, "login": user.login, "email": user.email, "role": user.role.value}


@router.get("/domains")
async def list_domains(
    q: str | None = Query(None),
    expiring: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(api_user),
) -> dict:
    flt = DomainFilter(q=q or None, expiring_days=expiring, page=page, page_size=page_size)
    items, total = await domains_svc.list_domains(session, user, flt)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [DomainRead.model_validate(d).model_dump(mode="json") for d in items],
    }


@router.get("/alerts")
async def list_alerts(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(api_user),
) -> dict:
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
    return {
        "items": [
            {
                "kind": e.kind,
                "severity": e.severity,
                "domain": fqdn,
                "fired_at": e.fired_at.isoformat() if e.fired_at else None,
                "payload": e.payload_json or {},
            }
            for e, fqdn in rows
        ]
    }
