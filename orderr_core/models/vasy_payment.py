"""
vasy_payments — read-only mirror of Vasy payments (money OUT). Header level,
one row per Payment No. Party = who was paid (supplier / expense head,
free-text). Distinct from CustomerReceipt (money IN) despite Vasy reusing the
PAY#### prefix — different series, different table.

Real export columns (2026-07-10):
  # · Payment No · Party Name · Payment Mode · Date · Amount · Status · Created By
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class VasyPayment(Base):
    __tablename__ = "vasy_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    payment_no: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    party_name: Mapped[str] = mapped_column(String, nullable=False)
    party_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    mode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)   # cash | online | cheque
    payment_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
