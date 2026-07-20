"""VirusTotal domain reputation results (SPEC FR-CK-3, §4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class VtResult(Base):
    __tablename__ = "vt_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), index=True
    )
    harmless: Mapped[int] = mapped_column(Integer, default=0)
    malicious: Mapped[int] = mapped_column(Integer, default=0)
    suspicious: Mapped[int] = mapped_column(Integer, default=0)
    undetected: Mapped[int] = mapped_column(Integer, default=0)
    reputation: Mapped[int] = mapped_column(Integer, default=0)
    categories_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    last_analysis_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
