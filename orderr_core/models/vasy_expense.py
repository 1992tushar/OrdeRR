"""
vasy_expenses — read-only mirror of Vasy expenses (opex). Header level, one row
per Expense No. Party is the expense head/vendor (free-text). Carries the
Paid / UnPaid split (unpaid = a small payable).

Real export columns (2026-07-10):
  Sr.No. · Expense No. · Expense Date · Party Name · Total · Paid · UnPaid ·
  Branch · Created By · Created From
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class VasyExpense(Base):
    __tablename__ = "vasy_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    expense_no: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    expense_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    party_name: Mapped[str] = mapped_column(String, nullable=False)
    party_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    total: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    paid: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    unpaid: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    # From the Expense Register export's hidden "Payment Data" column
    # ('Cash : 1200' / 'bank : 300') — per-expense payment-mode split, used by
    # the Cash Book cross-check. NULL = that export hasn't covered this row yet.
    cash_paid: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)
    noncash_paid: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
