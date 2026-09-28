"""People directory + alert routing rules (admin) — T97."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_role
from app.models.user import Role, User
from app.services import companies as companies_svc
from app.services import people as svc
from app.services.alert_workflow import KIND_LABELS
from app.templating import templates

router = APIRouter(prefix="/people", tags=["web-people"])
admin_required = require_role(Role.admin)

ROUTE_KIND_LABELS = {"*": "Любой вид", **KIND_LABELS}


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


async def _users(session: AsyncSession) -> list[User]:
    return list((await session.execute(select(User).order_by(User.login))).scalars().all())


async def _render_list(
    request: Request,
    session: AsyncSession,
    user: User,
    *,
    error: str | None = None,
    form: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    all_people = await svc.list_people(session)
    return templates.TemplateResponse(
        request,
        "people/list.html",
        {
            "user": user,
            "people": all_people,
            "routes": await svc.list_routes(session),
            "companies": await companies_svc.list_companies(session, user),
            "projects": await companies_svc.list_projects(session, user),
            "users": await _users(session),
            "kind_labels": ROUTE_KIND_LABELS,
            "linked": {p.user_id: p for p in all_people if p.user_id},
            "error": error,
            "form": form or {},
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
async def people_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
) -> HTMLResponse:
    return await _render_list(request, session, user)


@router.post("")
async def person_create(
    request: Request,
    kind: str = Form("person"),
    name: str = Form(""),
    handle: str = Form(""),
    discord_id: str = Form(""),
    telegram_username: str = Form(""),
    user_id: str = Form(""),
    note: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    try:
        await svc.create_person(
            session,
            kind=kind,
            name=name,
            handle=handle,
            discord_id=discord_id,
            telegram_username=telegram_username,
            user_id=_int_or_none(user_id),
            note=note,
            actor_id=user.id,
        )
    except svc.PersonError as exc:
        # Validation fails before any write, so there is nothing to roll back (and a
        # rollback would expire the request's `user`, breaking the template render).
        form = {
            "kind": kind,
            "name": name,
            "handle": handle,
            "discord_id": discord_id,
            "telegram_username": telegram_username,
            "user_id": _int_or_none(user_id),
            "note": note,
        }
        return await _render_list(
            request, session, user, error=str(exc), form=form, status_code=422
        )
    return RedirectResponse("/people", status_code=status.HTTP_303_SEE_OTHER)


# NOTE: the /routes handlers must be registered before /{person_id} ones, otherwise
# POST /people/routes is captured by POST /people/{person_id} (and fails int parsing).


@router.post("/routes")
async def route_set(
    request: Request,
    scope: str = Form("global"),
    kind: str = Form("*"),
    person_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    try:
        pid = _int_or_none(person_id)
        if pid is None:
            raise svc.PersonError("Выберите, кому назначать.")
        company_id, project_id = svc.parse_scope(scope)
        await svc.set_route(
            session,
            company_id=company_id,
            project_id=project_id,
            kind=kind,
            person_id=pid,
            actor_id=user.id,
        )
    except svc.PersonError as exc:
        return await _render_list(request, session, user, error=str(exc), status_code=422)
    return RedirectResponse("/people#routes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/routes/{route_id}/delete")
async def route_delete(
    route_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    await svc.delete_route(session, route_id, actor_id=user.id)
    return RedirectResponse("/people#routes", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{person_id}/edit", response_class=HTMLResponse)
async def person_edit_page(
    request: Request,
    person_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
) -> HTMLResponse:
    person = await svc.get_person(session, person_id)
    if person is None:
        return RedirectResponse("/people", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        "people/edit.html",
        {"user": user, "person": person, "users": await _users(session), "error": None},
    )


@router.post("/{person_id}")
async def person_update(
    request: Request,
    person_id: int,
    kind: str = Form("person"),
    name: str = Form(""),
    handle: str = Form(""),
    discord_id: str = Form(""),
    telegram_username: str = Form(""),
    user_id: str = Form(""),
    note: str = Form(""),
    is_active: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    person = await svc.get_person(session, person_id)
    if person is None:
        return RedirectResponse("/people", status_code=status.HTTP_303_SEE_OTHER)
    try:
        await svc.update_person(
            session,
            person,
            kind=kind,
            name=name,
            handle=handle,
            discord_id=discord_id,
            telegram_username=telegram_username,
            user_id=_int_or_none(user_id),
            note=note,
            is_active=is_active is not None,
            actor_id=user.id,
        )
    except svc.PersonError as exc:
        return templates.TemplateResponse(
            request,
            "people/edit.html",
            {"user": user, "person": person, "users": await _users(session), "error": str(exc)},
            status_code=422,
        )
    return RedirectResponse("/people", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{person_id}/delete")
async def person_delete(
    person_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    person = await svc.get_person(session, person_id)
    if person is not None:
        await svc.delete_person(session, person, actor_id=user.id)
    return RedirectResponse("/people", status_code=status.HTTP_303_SEE_OTHER)
