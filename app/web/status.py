"""Status page for tracking domains (T99) — read-only, for company employees.

Anyone with a Google Workspace account on an allowed e-mail domain (e.g.
``@adera.agency``) can sign in with Google and see the tracking domains of the company
the rule points at. This is NOT a DomainGuard account: it is a separate short-lived
session (Redis + ``dg_status`` cookie scoped to ``/status``) that grants nothing but
this page. A signed-in DomainGuard user sees the page too (with their own scope).

The access rule is re-checked on every request, so deleting it revokes access at once.
"""

from __future__ import annotations

import json
import secrets

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.db import get_session
from app.deps import current_user_optional, redis_dep
from app.models.user import User
from app.services import domains as domains_svc
from app.services import google_oauth
from app.services import tracking as svc
from app.templating import templates

router = APIRouter(prefix="/status", tags=["web-status"])

STATUS_COOKIE = "dg_status"
STATUS_TTL_SECONDS = 12 * 3600  # absolute: re-login with Google twice a day
PURPOSE_COOKIE = "dg_oauth_purpose"
_PREFIX = "status_session:"


def _is_secure() -> bool:
    return settings.environment == "production"


async def _viewer(
    request: Request, session: AsyncSession, redis: aioredis.Redis, user: User | None
) -> tuple[str, set[int] | None] | None:
    """(who, allowed project ids) for this request, or None if not signed in."""
    token = request.cookies.get(STATUS_COOKIE, "")
    if token:
        raw = await redis.get(_PREFIX + token)
        if raw:
            data = json.loads(raw)
            rules = await svc.get_access_rules(session)
            rule = next((r for r in rules if r.email_domain == data.get("email_domain")), None)
            if rule is not None:  # the rule may have been removed since sign-in
                return data["email"], await svc.scope_for_rule(session, rule)
    if user is not None:
        return user.login, await domains_svc.allowed_project_ids(session, user)
    return None


def _login_page(request: Request, *, error: str | None = None, code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "status/login.html",
        {"error": error, "google_enabled": settings.google_oauth_enabled, "wide": False},
        status_code=code,
    )


@router.get("", include_in_schema=False)
async def status_root():
    return RedirectResponse("/status/tracking", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/tracking", response_class=HTMLResponse)
async def status_tracking(
    request: Request,
    partial: str | None = None,
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(redis_dep),
    user: User | None = Depends(current_user_optional),
) -> HTMLResponse:
    viewer = await _viewer(request, session, redis, user)
    if viewer is None:
        if partial:  # the auto-refresh of an expired session: tell HTMX to reload
            return HTMLResponse("", status_code=401, headers={"HX-Refresh": "true"})
        return _login_page(request)
    who, allowed = viewer
    board = await svc.build_board(session, allowed=allowed)
    ctx = {"board": board, "public": True, "can_manage": False, "who": who, "wide": True}
    if partial:
        return templates.TemplateResponse(request, "tracking/_board.html", ctx)
    return templates.TemplateResponse(request, "status/tracking.html", ctx)


@router.get("/login/google")
async def status_login_google(request: Request, session: AsyncSession = Depends(get_session)):
    if not settings.google_oauth_enabled:
        return _login_page(request, error="Вход через Google ещё не настроен.", code=503)
    from app.web.oauth import STATE_COOKIE, _redirect_uri

    rules = await svc.get_access_rules(session)
    hint = rules[0].email_domain if len(rules) == 1 else None
    state = google_oauth.new_state()
    url = google_oauth.build_authorize_url(
        state=state, redirect_uri=_redirect_uri(request), hd=hint
    )
    response = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    for name, value in ((STATE_COOKIE, state), (PURPOSE_COOKIE, "status")):
        response.set_cookie(
            name, value, max_age=600, httponly=True, secure=_is_secure(), samesite="lax"
        )
    return response


async def complete_google_login(
    request: Request,
    session: AsyncSession,
    redis: aioredis.Redis,
    identity: google_oauth.GoogleIdentity,
):
    """Finish a status-page Google sign-in (called from the shared OAuth callback)."""
    from app.web.oauth import STATE_COOKIE

    rules = await svc.get_access_rules(session)
    rule = svc.match_access(rules, identity.email, identity.hd) if identity.email_verified else None
    if rule is None:
        domains = ", ".join(f"@{r.email_domain}" for r in rules) or "—"
        response = _login_page(
            request,
            error=f"Доступ только для рабочих Google-аккаунтов {domains}. "
            f"Вы вошли как {identity.email}.",
            code=403,
        )
    else:
        token = secrets.token_urlsafe(32)
        await redis.set(
            _PREFIX + token,
            json.dumps({"email": identity.email, "email_domain": rule.email_domain}),
            ex=STATUS_TTL_SECONDS,
        )
        await record_audit(
            session,
            actor_id=None,
            action="status_login",
            entity_type="status_page",
            diff={"email": identity.email},
        )
        await session.commit()
        response = RedirectResponse("/status/tracking", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            STATUS_COOKIE,
            token,
            max_age=STATUS_TTL_SECONDS,
            httponly=True,
            secure=_is_secure(),
            samesite="lax",
            path="/status",
        )
    response.delete_cookie(STATE_COOKIE)
    response.delete_cookie(PURPOSE_COOKIE)
    return response


@router.post("/logout")
async def status_logout(request: Request, redis: aioredis.Redis = Depends(redis_dep)):
    token = request.cookies.get(STATUS_COOKIE, "")
    if token:
        await redis.delete(_PREFIX + token)
    response = RedirectResponse("/status/tracking", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(STATUS_COOKIE, path="/status")
    return response
