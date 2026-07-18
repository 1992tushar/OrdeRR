"""
bad_debts — customer balances the owner has written off as unrecoverable
(hotel closed, owner absconded, dispute…).

One row per customer (unique customer_id) — this is a whole-customer flag in
the running-account model, not an invoice-level adjustment. `amount` records
the balance at the moment of write-off for the audit trail; the analytics
screens exclude the customer's CURRENT snapshot balance from AR while the
flag exists. OrdeRR-side overlay only — the Vasy ledger is never touched, so
removing the row instantly restores the customer to AR.
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class BadDebt(Base):
    __tablename__ = "bad_debts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=False, unique=True, index=True
    )
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # balance when written off
    reason: Mapped[str] = mapped_column(String, nullable=False)        # e.g. "Hotel closed"
    note: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # free text
    written_off_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
