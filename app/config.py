"""Application configuration loaded from environment variables.

All runtime configuration comes from the environment (12-factor); secrets are never
hard-coded. See ``.env.example`` for the full list of supported variables.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings.

    Values are read from environment variables (case-insensitive) or a local
    ``.env`` file during development. In production every value is supplied by the
    container environment.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General ---
    app_name: str = "DomainGuard"
    environment: str = "development"
    debug: bool = False
    # Display / scheduling timezone. Storage is always UTC (see CLAUDE.md).
    timezone: str = "Europe/Kyiv"

    # --- Datastores ---
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://domainguard:domainguard@postgres:5432/domainguard",
    )
    redis_url: RedisDsn = Field(default="redis://redis:6379/0")

    # --- Security ---
    # Fernet master key used to encrypt secrets at rest (SEC-1). Generated per
    # deployment; kept only in the environment, never in the DB or git.
    dg_master_key: str = Field(default="", repr=False)

    # --- Google OAuth (optional sign-in for existing users, T37) ---
    # Supplied at deploy from Google Cloud Console; the secret is never logged.
    # When unset, the Google button is hidden and the routes 404 (feature dormant).
    google_client_id: str = ""
    google_client_secret: str = Field(default="", repr=False)
    # Public callback URL registered with Google. If blank it is derived from the
    # incoming request base URL (…/auth/google/callback).
    google_redirect_uri: str = ""

    # Public base URL of the deployment (scheme+host, no trailing slash), e.g.
    # https://domains.zimbabwe-inc.com. Used to build absolute OAuth/MCP URLs
    # (issuer, consent redirect). Falls back to localhost for dev.
    public_base_url: str = "http://localhost:8080"

    # --- HTTP server ---
    host: str = "0.0.0.0"  # noqa: S104 — bound inside the container network only
    port: int = 8000

    @property
    def sync_database_url(self) -> str:
        """Synchronous DSN (psycopg-style) for tools that need it, e.g. Alembic offline."""
        return str(self.database_url).replace("+asyncpg", "")

    @property
    def google_oauth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance for the process lifetime."""
    return Settings()


settings = get_settings()
