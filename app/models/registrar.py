"""Registrar and registrar-account models (SPEC §3.4, §4).

Registrar accounts are independent of companies (FR-RG-1): one account may hold
domains of several companies; a domain is bound to a project manually or via the
"unassigned" queue. Credentials are encrypted at rest and masked in UI/logs.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Registrar(Base):
    __tablename__ = "registrars"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    connector_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # namecheap|...

    accounts: Mapped[list[RegistrarAccount]] = relationship(
        back_populates="registrar", cascade="all, delete-orphan", lazy="selectin"
    )


class RegistrarAccount(Base):
    __tablename__ = "registrar_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    registrar_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("registrars.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(128))
    credentials_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    registrar: Mapped[Registrar] = relationship(back_populates="accounts")


class UnassignedDomain(Base):
    """A domain discovered by a registrar sync that has no project yet (FR-RG-5)."""

    __tablename__ = "unassigned_domains"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    registrar_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("registrar_accounts.id", ondelete="CASCADE"), index=True
    )
    fqdn: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_renew: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
