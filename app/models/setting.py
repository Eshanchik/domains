"""Key/value settings, values encrypted at rest (SPEC §4 — Setting)."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Encrypted value (Fernet token). Never returned in the clear via the API.
    value_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
