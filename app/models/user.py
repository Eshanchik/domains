"""User, role, and access-scope models (SPEC §2).

UserScope pins a user to a set of companies and/or projects. The Company/Project
tables are introduced in T03; the ``company_id`` / ``project_id`` columns here are
plain nullable integers for now and gain foreign keys in that task.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Role(enum.StrEnum):
    """RBAC roles (SPEC §2). Ordered from most to least privileged."""

    admin = "admin"
    manager = "manager"
    viewer = "viewer"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="user_role", native_enum=False, length=16),
        default=Role.viewer,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 2FA (TOTP): secret encrypted at rest; enabled only after a verified code.
    totp_secret_enc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    scopes: Mapped[list[UserScope]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User {self.login} role={self.role.value}>"


class UserScope(Base):
    """A single access-scope grant for a user.

    A row scopes the user to a company (all its projects) or to a single project.
    A user with the ``admin`` role ignores scopes (full access). A user with *no*
    scope rows and a non-admin role sees nothing.
    """

    __tablename__ = "user_scopes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )

    user: Mapped[User] = relationship(back_populates="scopes")
