"""Domain registry models (SPEC §3.2, §4).

Registrar / RegistrarAccount tables arrive in T16; ``registrar_id`` and
``registrar_account_id`` are nullable integers without FKs until then (same pattern
UserScope used before T03).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.company import Tag


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )

    fqdn: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    punycode: Mapped[str] = mapped_column(String(253), index=True)
    tld: Mapped[str] = mapped_column(String(63), index=True)

    # Registrar linkage — FKs added in T16.
    registrar_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    registrar_account_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    registration_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expiry_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    updated_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # None = unknown (SPEC: да/нет/неизвестно).
    auto_renew: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    epp_statuses: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    nameservers: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    registrant: Mapped[str | None] = mapped_column(String(255), nullable=True)

    renewal_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    renewal_currency: Mapped[str] = mapped_column(String(3), default="USD")
    renewal_period_months: Mapped[int] = mapped_column(Integer, default=12)

    ssl_extra_hosts: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Per-field provenance: {field_name: "manual"|"csv"|"api-*"|"rdap"} (SPEC merge rules).
    field_sources: Mapped[dict] = mapped_column(JSONB, default=dict)

    responsible_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tags: Mapped[list[Tag]] = relationship(secondary="domain_tags", lazy="selectin")
    history: Mapped[list[DomainFieldHistory]] = relationship(
        back_populates="domain",
        cascade="all, delete-orphan",
        order_by="DomainFieldHistory.changed_at.desc()",
    )


class DomainTag(Base):
    """Many-to-many association between domains and tags (FR-CO-3)."""

    __tablename__ = "domain_tags"

    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class DomainFieldHistory(Base):
    """Timeline of changes to key domain fields (FR-DM-5)."""

    __tablename__ = "domain_field_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(64))
    old: Mapped[str | None] = mapped_column(Text, nullable=True)
    new: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    domain: Mapped[Domain] = relationship(back_populates="history")
