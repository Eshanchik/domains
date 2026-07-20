"""Shared test fixtures."""

from __future__ import annotations

import os

# Force the test profile before any app module is imported so the SQLAlchemy engine
# uses NullPool. Each TestClient spins its own event loop; a pooled asyncpg
# connection bound to a previous loop would raise "attached to a different loop".
os.environ["ENVIRONMENT"] = "test"
# A fixed, valid Fernet key for encrypting secrets in tests (not a real secret).
os.environ.setdefault("DG_MASTER_KEY", "2f4lEgCux7eN2sivGri_2plV3lhH3soDWV4g2PwwwGk=")

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A TestClient bound to a freshly built app instance."""
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
