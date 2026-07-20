"""FastAPI application factory.

Keeps app construction in a factory so tests can build isolated instances and the
ASGI server (uvicorn) imports a ready application via ``app.main:app``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import health, metrics, v1
from app.config import settings
from app.deps import NotAuthenticated
from app.log import configure_logging
from app.web import alerts as web_alerts
from app.web import auth as web_auth
from app.web import channels as web_channels
from app.web import companies as web_companies
from app.web import domains as web_domains
from app.web import healthchecks as web_healthchecks
from app.web import import_web, settings_web
from app.web import payments as web_payments
from app.web import registrars as web_registrars
from app.web import tokens as web_tokens
from app.web import twofa as web_twofa
from app.web import users as web_users
from app.web import webhooks as web_webhooks

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    configure_logging()  # structured JSON logs (NFR-5)
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
    app.include_router(metrics.router)
    app.include_router(v1.router)
    app.include_router(web_auth.router)
    app.include_router(web_users.router)
    app.include_router(web_companies.router)
    app.include_router(web_domains.router)
    app.include_router(import_web.router)
    app.include_router(settings_web.router)
    app.include_router(web_healthchecks.router)
    app.include_router(web_channels.router)
    app.include_router(web_alerts.router)
    app.include_router(web_payments.router)
    app.include_router(web_registrars.router)
    app.include_router(web_tokens.router)
    app.include_router(web_webhooks.router)
    app.include_router(web_twofa.router)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


app = create_app()
