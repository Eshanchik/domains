"""People directory + alert routing (T97).

A :class:`Person` is someone (or a group, e.g. a Discord role like ``@ops``) that an
alert can be assigned to and who gets @-mentioned in Discord/Telegram. People are
independent of login accounts: a person may optionally be linked to a DomainGuard
user, but most people in a chat never log in.

An :class:`AlertRoute` maps an alert *kind* to the person responsible for it at a
scope — project, company or global (both ids NULL). ``kind='*'`` matches any kind.
Resolution is most-specific-first (project → company → global), like channel routing.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

PERSON_KINDS = ("person", "group")


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), default="person")  # person|group
    name: Mapped[str] = mapped_column(String(128))
    # Short lowercase slug used for @mentions in comments (``@vasya``, ``@ops``).
    handle: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Discord snowflake: a user id for a person, a role id for a group.
    discord_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Telegram username without the leading "@".
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Person @{self.handle} {self.kind}>"


class AlertRoute(Base):
    __tablename__ = "alert_routes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32))  # an alert kind or "*"
    person_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("people.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# One rule per (scope, kind); NULL scope ids are folded to 0 so the global scope is
# unique too (a plain UNIQUE constraint treats NULLs as distinct).
Index(
    "uq_alert_route_scope_kind",
    func.coalesce(AlertRoute.company_id, 0),
    func.coalesce(AlertRoute.project_id, 0),
    AlertRoute.kind,
    unique=True,
)
