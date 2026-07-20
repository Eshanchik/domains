"""Custom health-check models (SPEC FR-CK-4).

A domain has 0..N health-checks (domains without any are not availability-checked).
Each check has its own URL/expectations/interval and a small up/down/unknown state
machine driven by consecutive failures.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class HealthCheck(Base):
    __tablename__ = "health_checks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(4), default="GET")
    follow_redirects: Mapped[bool] = mapped_column(Boolean, default=False)
    # e.g. "301,302" or "200-299"
    expected_statuses: Mapped[str] = mapped_column(String(64), default="200-299")
    location_pattern: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body_substring: Mapped[str | None] = mapped_column(String(512), nullable=True)
    timeout_s: Mapped[int] = mapped_column(Integer, default=10)
    interval_min: Mapped[int] = mapped_column(Integer, default=15)
    fail_threshold: Mapped[int] = mapped_column(Integer, default=3)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    state: Mapped[str] = mapped_column(String(8), default="unknown")  # up | down | unknown
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    results: Mapped[list[HealthCheckResult]] = relationship(
        back_populates="healthcheck",
        cascade="all, delete-orphan",
        order_by="HealthCheckResult.checked_at.desc()",
    )


class HealthCheckResult(Base):
    __tablename__ = "health_check_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    healthcheck_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("health_checks.id", ondelete="CASCADE"), index=True
    )
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    healthcheck: Mapped[HealthCheck] = relationship(back_populates="results")
