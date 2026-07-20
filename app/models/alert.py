"""Alert rules and events (SPEC §3.6, §4).

AlertEvent has at most one *active* row per ``dedupe_key`` (a partial unique index),
which is what prevents repeated runs from spamming. Crossing a tighter threshold
uses a different dedupe_key, so it fires a fresh event.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AlertRule(Base):
    """Optional per-scope override of default thresholds/severity."""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope: Mapped[str] = mapped_column(String(16))  # global|company|project|domain
    scope_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    condition_type: Mapped[str] = mapped_column(String(32))  # expiry|ssl|vt_malicious|health
    threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(String(8), default="medium")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        # Only one active event per dedupe_key (dedup; SPEC FR-AL-4).
        Index(
            "uq_alert_event_active",
            "dedupe_key",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("ix_alert_events_domain_state", "domain_id", "state"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    domain_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(32))  # expiry|ssl|vt_malicious|health_down
    dedupe_key: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str] = mapped_column(String(8), default="medium")  # high|medium|low
    state: Mapped[str] = mapped_column(String(8), default="active")  # active|resolved
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
