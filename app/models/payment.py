"""Domain renewal payment records (SPEC FR-CT-2, §4).

Each payment fixes the exchange rate to USD at the time it is recorded, so historical
totals never shift when rates change.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), index=True
    )
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    rate_to_usd: Mapped[Decimal] = mapped_column(Numeric(16, 6), default=1)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
