"""Check scheduling model (SPEC §3.5, §4).

Each domain has a per-type ``next_check_at``; the scheduler enqueues only mature
rows (indexed by ``(type, next_check_at)``). Check *results* tables arrive with the
individual check tasks (T07+).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CheckType(enum.StrEnum):
    rdap = "rdap"
    whois = "whois"
    ssl = "ssl"
    vt = "vt"
    dns = "dns"
    healthcheck = "healthcheck"


class CheckSchedule(Base):
    __tablename__ = "check_schedule"
    __table_args__ = (Index("ix_check_schedule_type_next", "type", "next_check_at"),)

    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True
    )
    type: Mapped[CheckType] = mapped_column(
        Enum(CheckType, name="check_type", native_enum=False, length=16), primary_key=True
    )
    next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
