"""
cash_entries — manual Cash Book lines for drawer movements Vasy never sees
(spec: CASH_BOOK_REQUIREMENTS.md §3).

The Cash Book itself is a read-only VIEW over imported mirrors (cash receipts
in, cash payments out); these rows are its ONLY writes:

  drawing        OUT  owner took cash (recorded nowhere else — owner confirmed)
  bank_deposit   OUT  cash moved from drawer to bank
  float_given    OUT  staff float handed out
  float_returned IN   staff float came back
  opening_set    —    anchor: "at start of entry_date the drawer held ₹amount"
                      (used before the first signed 5-day close exists)
  adjustment     IN/OUT correction line (note mandatory)
  other          IN/OUT anything else
  spot_count     —    "I counted ₹amount in the drawer on this day" — stored
                      for the variance check, never part of the running flow
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class CashEntry(Base):
    __tablename__ = "cash_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    # direction is derived for most types; stored so adjustment/other carry theirs.
    # NULL for opening_set / spot_count (not flows).
    direction: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)  # 'in' | 'out'
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
