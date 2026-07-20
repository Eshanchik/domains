"""SSL certificate observations (SPEC FR-CK-2, §4)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ARRAY, BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SslCertificate(Base):
    __tablename__ = "ssl_certificates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), index=True
    )
    host: Mapped[str] = mapped_column(String(253))
    issuer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    san: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
