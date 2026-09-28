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
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
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
    # When this event was delivered (instant or digest). NULL = not yet notified, so it
    # is picked up by the next digest exactly once; each threshold crossing is a new event.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Workflow (T97): who owns the alert (a person or a group from the people directory,
    # set by routing rules or by hand), who took it into work, who closed it and why.
    assignee_person_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("people.id", ondelete="SET NULL"), nullable=True, index=True
    )
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


ACTIVITY_KINDS = ("notified", "digest", "assigned", "acked", "comment", "resolved")


class AlertActivity(Base):
    """One entry of an alert's timeline: delivery, assignment, ack, comment, resolve.

    ``actor_user_id`` is NULL for system entries (instant dispatch, digest, routing).
    ``data_json`` carries structured context (channel names, mentioned person ids,
    assignee from/to) so the card can render it without re-deriving history.
    """

    __tablename__ = "alert_activity"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alert_event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alert_events.id", ondelete="CASCADE"), index=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
