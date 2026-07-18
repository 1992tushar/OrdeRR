"""
close_periods — a signed-off 5-Day Close (P2). One row per closed window,
upserted on (from_date, to_date) so re-signing updates rather than duplicates.

Stores the two manual cash figures (opening + counted), the computed cash
movement/gap, the closing AR/AP balances and their gaps at sign-off, and a JSON
snapshot of the exceptions that were outstanding — so the next window's opening
auto-fills and the close accrues an audit history. Read-mostly: written only by
the sign-off action, never by imports.
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, Text, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class ClosePeriod(Base):
    __tablename__ = "close_periods"
    __table_args__ = (
        UniqueConstraint("from_date", "to_date", name="uq_close_window"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    from_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    to_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # manual cash inputs (keyed at sign-off)
    opening_cash: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)
    counted_cash: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)
    drawings: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    # computed cash reconciliation (snapshotted at sign-off)
    cash_movement: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    cash_gap: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)

    # closing balances + tie-out gaps at sign-off (NULL where indeterminate)
    closing_debtors: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)
    closing_creditors: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)
    ar_gap: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)

    # exceptions snapshot (JSON list of {title, count, severity}) + counts
    exceptions_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exception_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    signed_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self):
        return f"<ClosePeriod {self.from_date}→{self.to_date} signed={self.signed_at}>"
