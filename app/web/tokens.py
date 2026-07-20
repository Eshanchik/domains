"""Personal API-token management (any authenticated user) — SPEC FR-API-1."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_user
from app.models.user import User
from app.services import api_tokens as svc
from app.templating import templates

router = APIRouter(prefix="/tokens", tags=["web-tokens"])


@router.get("", response_class=HTMLResponse)
async def tokens_page(
    request: Request,
    new_token: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    tokens = await svc.list_for_user(session, user.id)
    return templates.TemplateResponse(
        request, "tokens/list.html", {"user": user, "tokens": tokens, "new_token": new_token}
    )


@router.post("")
async def token_create(
    request: Request,
    name: str = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    _token, plaintext = await svc.create_token(session, user, name.strip() or "token")
    # Re-render with the plaintext shown exactly once.
    tokens = await svc.list_for_user(session, user.id)
    return templates.TemplateResponse(
        request, "tokens/list.html", {"user": user, "tokens": tokens, "new_token": plaintext}
    )


@router.post("/{token_id}/revoke")
async def token_revoke(
    token_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
):
    from app.models.api import ApiToken

    token = await session.get(ApiToken, token_id)
    if token is not None and token.user_id == user.id:
        await svc.revoke(session, token, actor_id=user.id)
    return RedirectResponse("/tokens", status_code=status.HTTP_303_SEE_OTHER)
