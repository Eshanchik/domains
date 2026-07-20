"""Notification channels and delivery log (SPEC §3.6, §4).

A channel is attached to a company, a project, or marked as the global default.
Alert routing resolves domain → project → company → global (first level that has
channels wins; all channels on that level receive the message).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(16), default="telegram")
    name: Mapped[str] = mapped_column(String(255))
    # Encrypted JSON config (e.g. {"chat_id": "..."}); never returned in the clear.
    config_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    mode: Mapped[str] = mapped_column(String(8), default="both")  # instant | digest | both
    digest_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM"
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class NotificationLog(Base):
    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # FK to alert_events is added in T12; digest_id in T13. Nullable ids for now.
    alert_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    digest_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("notification_channels.id", ondelete="CASCADE"), index=True
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivery_status: Mapped[str] = mapped_column(String(16))  # sent | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
