"""Domain import pages: bulk textarea + CSV upload with preview (FR-DM-2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_role
from app.models.user import Role, User
from app.services import companies as companies_svc
from app.services import import_domains as svc
from app.templating import templates

router = APIRouter(tags=["web-import"])
manager_required = require_role(Role.manager)


@router.get("/import", response_class=HTMLResponse)
async def import_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
) -> HTMLResponse:
    projects = await companies_svc.list_projects(session, user)
    return templates.TemplateResponse(
        request, "import/form.html", {"user": user, "projects": projects}
    )


@router.post("/import", response_class=HTMLResponse)
async def import_run(
    request: Request,
    content: str = Form(""),
    fmt: str = Form("bulk"),
    default_project_id: str = Form(""),
    commit: str = Form("false"),
    csv_file: UploadFile | None = File(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
) -> HTMLResponse:
    # An uploaded CSV wins over the pasted textarea.
    if csv_file is not None and csv_file.filename:
        raw = await csv_file.read()
        content = raw.decode("utf-8", errors="replace")
        fmt = "csv"

    rows = svc.parse_csv(content) if fmt == "csv" else svc.parse_bulk(content)
    source = "csv" if fmt == "csv" else "manual"
    project_id = int(default_project_id) if default_project_id.strip() else None

    do_commit = commit == "true"
    report = await svc.run_import(
        session,
        user,
        rows,
        default_project_id=project_id,
        source=source,
        actor_id=user.id,
        dry_run=not do_commit,
    )
    return templates.TemplateResponse(
        request,
        "import/result.html",
        {
            "user": user,
            "report": report,
            "committed": do_commit,
            "content": content,
            "fmt": fmt,
            "default_project_id": default_project_id,
        },
        status_code=status.HTTP_200_OK,
    )
