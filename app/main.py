"""FastAPI application factory.

Keeps app construction in a factory so tests can build isolated instances and the
ASGI server (uvicorn) imports a ready application via ``app.main:app``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import health
from app.config import settings
from app.deps import NotAuthenticated
from app.web import auth as web_auth
from app.web import companies as web_companies
from app.web import domains as web_domains
from app.web import users as web_users

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
    )

    # Unauthenticated access to a protected page → redirect to the login screen.
    @app.exception_handler(NotAuthenticated)
    async def _redirect_to_login(request: Request, _exc: NotAuthenticated) -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    app.include_router(health.router)
    app.include_router(web_auth.router)
    app.include_router(web_users.router)
    app.include_router(web_companies.router)
    app.include_router(web_domains.router)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


app = create_app()
