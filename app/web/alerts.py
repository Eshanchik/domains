"""Active alerts page + alert detail (SPEC FR-UI-4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import redis_dep, require_role, require_user
from app.models.alert import AlertEvent
from app.models.company import Company, Project
from app.models.domain import Domain
from app.models.person import Person
from app.models.user import Role, User
from app.services import alert_workflow as workflow
from app.services import alerts as alerts_svc
from app.services import companies as companies_svc
from app.services import domains as domains_svc
from app.services import notifications as notif
from app.services import people as people_svc
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


def _int_or_none(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


ALERT_KINDS = ["expiry", "ssl", "vt_malicious", "health_down", "ns_change"]
ALERT_SEVERITIES = ["high", "medium", "low"]


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_list(
    request: Request,
    company_id: str | None = None,
    project_id: str | None = None,
    severity: str | None = None,
    kind: str | None = None,
    owner: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    company_id = _int_or_none(company_id)
    project_id = _int_or_none(project_id)
    severity = severity or None
    kind = kind or None
    owner = owner or None
    allowed = await domains_svc.allowed_project_ids(session, user)
    stmt = (
        select(AlertEvent, Domain.fqdn, Project.name, Company.name, Person)
        .join(Domain, Domain.id == AlertEvent.domain_id)
        .join(Project, Project.id == Domain.project_id)
        .join(Company, Company.id == Project.company_id)
        .outerjoin(Person, Person.id == AlertEvent.assignee_person_id)
        .where(AlertEvent.state == "active", Domain.is_active.is_(True))
        .order_by(AlertEvent.fired_at.desc())
    )
    if allowed is not None:
        stmt = (
            stmt.where(Domain.project_id.in_(allowed))
            if allowed
            else stmt.where(Domain.id.is_(None))
        )
    if company_id is not None:
        stmt = stmt.where(Company.id == company_id)
    if project_id is not None:
        stmt = stmt.where(Project.id == project_id)
    if severity in ALERT_SEVERITIES:
        stmt = stmt.where(AlertEvent.severity == severity)
    if kind in ALERT_KINDS:
        stmt = stmt.where(AlertEvent.kind == kind)
    me = await people_svc.person_for_user(session, user.id)
    if owner == "none":
        stmt = stmt.where(AlertEvent.assignee_person_id.is_(None))
    elif owner == "me":
        stmt = stmt.where(AlertEvent.assignee_person_id == (me.id if me is not None else -1))
    elif _int_or_none(owner) is not None:
        stmt = stmt.where(AlertEvent.assignee_person_id == _int_or_none(owner))
    raw = (await session.execute(stmt)).all()
    now = datetime.now(UTC)
    rows = [
        {
            "event": event,
            "fqdn": fqdn,
            "project": project,
            "company": company,
            "owner": person,
            "age": format_age(now - event.fired_at),
        }
        for event, fqdn, project, company, person in raw
    ]
    return templates.TemplateResponse(
        request,
        "alerts/list.html",
        {
            "user": user,
            "rows": rows,
            "companies": await companies_svc.list_companies(session, user),
            "projects": await companies_svc.list_projects(session, user),
            "kinds": ALERT_KINDS,
            "severities": ALERT_SEVERITIES,
            "people": await people_svc.list_people(session, active_only=True),
            "me": me,
            "f": {
                "company_id": company_id,
                "project_id": project_id,
                "severity": severity,
                "kind": kind,
                "owner": owner,
            },
        },
    )


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
    msg: str | None = None,
    sent: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    found = await _load_alert_in_scope(session, user, alert_id)
    if found is None:
        return RedirectResponse("/alerts", status_code=status.HTTP_303_SEE_OTHER)
    event, domain = found
    card = await workflow.build_card(session, event, domain)
    return templates.TemplateResponse(
        request,
        "alerts/detail.html",
        {
            **card,
            "user": user,
            "can_act": user.role in (Role.admin, Role.manager),
            "notified": notified,
            "msg": msg,
            "sent": _int_or_none(sent),
        },
    )


def _deliver(session: AsyncSession, redis, event_id: int) -> workflow.Deliver:
    """Send synchronously from the api (it has egress); logged against the alert."""

    async def deliver(channel, text: str) -> bool:
        return await notif.send_to_channel(session, redis, channel, text, alert_event_id=event_id)

    return deliver


def _back(alert_id: int, msg: str, sent: int | None = None) -> RedirectResponse:
    q = f"?msg={msg}" + (f"&sent={sent}" if sent is not None else "")
    return RedirectResponse(f"/alerts/{alert_id}{q}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/alerts/{alert_id}/resolve")
async def alert_resolve(
    alert_id: int,
    note: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    found = await _load_alert_in_scope(session, user, alert_id)
    if found is not None:
        await alerts_svc.resolve_event(session, alert_id, actor_id=user.id, note=note)
    return RedirectResponse(f"/alerts/{alert_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/alerts/{alert_id}/assign")
async def alert_assign(
    alert_id: int,
    person_id: str = Form(""),
    notify: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
    redis=Depends(redis_dep),
    user: User = Depends(manager_required),
):
    """Set/clear the owner; optionally ping them in the domain's channels (Manager+)."""
    found = await _load_alert_in_scope(session, user, alert_id)
    if found is None:
        return RedirectResponse("/alerts", status_code=status.HTTP_303_SEE_OTHER)
    event, domain = found
    try:
        sent = await workflow.assign(
            session,
            event,
            domain,
            _int_or_none(person_id),
            actor=user,
            notify=notify is not None,
            deliver=_deliver(session, redis, event.id),
        )
    except people_svc.PersonError:
        return _back(alert_id, "bad_person")
    return _back(alert_id, "assigned", len(sent))


@router.post("/alerts/{alert_id}/ack")
async def alert_ack(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    """«Взял в работу» (Manager+)."""
    found = await _load_alert_in_scope(session, user, alert_id)
    if found is None:
        return RedirectResponse("/alerts", status_code=status.HTTP_303_SEE_OTHER)
    await workflow.ack(session, found[0], actor=user)
    return _back(alert_id, "acked")


@router.post("/alerts/{alert_id}/comment")
async def alert_comment(
    alert_id: int,
    body: str = Form(""),
    to_channel: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
    redis=Depends(redis_dep),
    user: User = Depends(manager_required),
):
    """Add a comment with @mentions; optionally post it to the channels (Manager+)."""
    found = await _load_alert_in_scope(session, user, alert_id)
    if found is None:
        return RedirectResponse("/alerts", status_code=status.HTTP_303_SEE_OTHER)
    event, domain = found
    try:
        sent = await workflow.comment(
            session,
            event,
            domain,
            body,
            actor=user,
            to_channel=to_channel is not None,
            deliver=_deliver(session, redis, event.id),
        )
    except people_svc.PersonError:
        return _back(alert_id, "empty_comment")
    return _back(alert_id, "commented", len(sent) if to_channel is not None else None)


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
        project, company = await alerts_svc.domain_location(session, domain)
        account = await alerts_svc.account_label(session, domain)
        owner = (
            await session.get(Person, event.assignee_person_id)
            if event.assignee_person_id is not None
            else None
        )
        for channel in await notif.resolve_channels(session, domain, purpose="instant"):
            text = alerts_svc.build_message(
                event,
                domain,
                project=project,
                company=company,
                account=account,
                mention=people_svc.mention(owner, channel.type) or None,
                url=workflow.alert_url(event.id),
            )
            if await notif.send_to_channel(session, redis, channel, text, alert_event_id=event.id):
                sent += 1
    return RedirectResponse(
        f"/alerts/{alert_id}?notified={sent}", status_code=status.HTTP_303_SEE_OTHER
    )
