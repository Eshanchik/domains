"""Check results, partitioned by month (SPEC §4, retention 12 months).

The table is declared ``PARTITION BY RANGE (checked_at)`` via a hand-written
migration; monthly partitions are created on demand (see
``app.checks.check_result_store.ensure_partition``). The ORM maps the parent table
for reads/writes — Postgres routes rows to the right partition. Autogeneration is
told to skip this table (and its partitions) in ``alembic/env.py``.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CheckStatus(enum.StrEnum):
    ok = "ok"
    warn = "warn"
    fail = "fail"
    stale = "stale"


class CheckResult(Base):
    __tablename__ = "check_result"
    __table_args__ = (
        Index("ix_check_result_domain_type", "domain_id", "type", "checked_at"),
        {"info": {"skip_autogenerate": True}},
    )

    # Partition key must be part of the PK, hence the composite (id, checked_at).
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    domain_id: Mapped[int] = mapped_column(BigInteger, index=True)
    type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(8))
    data_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
