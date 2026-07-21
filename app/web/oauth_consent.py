"""MCP OAuth consent page (T46), served by the api app (has session + templates).

The provider's ``authorize`` redirects the browser here. We require a logged-in user
who is allowed MCP (``user_may_use_mcp``), show an approve/deny screen, and on approval
mint a one-time authorization code (stored in the shared Redis OAuth store) and redirect
back to the OAuth client's ``redirect_uri`` — completing the flow the MCP SDK started.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from mcp.server.auth.provider import AuthorizationCode, construct_redirect_uri
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import current_user_optional, require_user
from app.mcp import oauth_store as store
from app.models.user import User
from app.services.auth import user_may_use_mcp
from app.templating import templates

router = APIRouter(tags=["mcp-oauth-consent"])

CODE_TTL = store.CODE_TTL


def _client_name(client) -> str:
    return (client.client_name if client and client.client_name else None) or "MCP-клиент"


@router.get("/oauth/consent", response_class=HTMLResponse)
async def consent_form(
    request: Request,
    rid: str = "",
    user: User | None = Depends(current_user_optional),
) -> HTMLResponse:
    authreq = await store.take_authreq(rid) if rid else None
    if authreq is None:
        return templates.TemplateResponse(
            request,
            "oauth/consent.html",
            {"error": "Запрос авторизации не найден или истёк. Повторите подключение."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if user is None:
        nxt = quote(f"/oauth/consent?rid={rid}", safe="")
        return RedirectResponse(f"/login?next={nxt}", status_code=status.HTTP_303_SEE_OTHER)

    client = await store.get_client(authreq["client_id"])
    allowed = user_may_use_mcp(user)
    return templates.TemplateResponse(
        request,
        "oauth/consent.html",
        {
            "rid": rid,
            "client_name": _client_name(client),
            "scopes": authreq.get("scopes") or [],
            "user": user,
            "allowed": allowed,
            "error": None,
        },
    )


@router.post("/oauth/consent")
async def consent_submit(
    rid: str = Form(...),
    decision: str = Form(...),
    session: AsyncSession = Depends(get_session),  # noqa: ARG001 — kept for symmetry
    user: User = Depends(require_user),
):
    authreq = await store.take_authreq(rid)
    if authreq is None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    redirect_uri = authreq["redirect_uri"]
    state = authreq.get("state")

    # Deny, or the user isn't permitted MCP → bounce back with an OAuth error.
    if decision != "approve" or not user_may_use_mcp(user):
        await store.drop_authreq(rid)
        return RedirectResponse(
            construct_redirect_uri(redirect_uri, error="access_denied", state=state),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    code = secrets.token_urlsafe(24)
    await store.put_code(
        AuthorizationCode(
            code=code,
            scopes=authreq.get("scopes") or [],
            expires_at=int(time.time()) + CODE_TTL,
            client_id=authreq["client_id"],
            code_challenge=authreq["code_challenge"],
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=authreq.get("redirect_uri_provided_explicitly", True),
            resource=authreq.get("resource"),
            subject=str(user.id),
        )
    )
    await store.drop_authreq(rid)
    return RedirectResponse(
        construct_redirect_uri(redirect_uri, code=code, state=state),
        status_code=status.HTTP_303_SEE_OTHER,
    )
