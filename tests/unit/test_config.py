"""Settings behaviour."""

from __future__ import annotations

from app.config import Settings


def test_defaults_and_sync_url() -> None:
    s = Settings()
    assert s.app_name == "DomainGuard"
    assert s.timezone == "Europe/Kyiv"  # display TZ; storage is UTC
    # The sync DSN drops the async driver marker for tooling that needs it.
    assert "+asyncpg" not in s.sync_database_url
    assert s.sync_database_url.startswith("postgresql://")


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "Custom")
    monkeypatch.setenv("DEBUG", "true")
    s = Settings()
    assert s.app_name == "Custom"
    assert s.debug is True


def test_master_key_is_not_reprd() -> None:
    # Secrets must never leak via repr/logs (SEC-2).
    s = Settings(dg_master_key="super-secret")
    assert "super-secret" not in repr(s)
