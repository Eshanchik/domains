"""Health/readiness endpoint behaviour (no live datastores required)."""

from __future__ import annotations

import app.api.health as health_module
from app.db import get_session
from app.main import create_app


def test_healthz_ok(client) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_reports_503_when_dependencies_down(monkeypatch) -> None:
    """When Postgres and Redis are unreachable, /readyz must return 503 and name
    the failing dependencies rather than raising (NFR-3)."""

    class _FailingSession:
        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    async def _override_session():
        yield _FailingSession()

    class _FailingRedis:
        async def ping(self):
            raise RuntimeError("redis down")

        async def aclose(self):
            return None

    monkeypatch.setattr(health_module, "get_redis", lambda: _FailingRedis())

    app = create_app()
    app.dependency_overrides[get_session] = _override_session

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        resp = client.get("/readyz")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"].startswith("error")
    assert body["checks"]["redis"].startswith("error")
