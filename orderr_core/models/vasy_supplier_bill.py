"""
vasy_supplier_bills — read-only mirror of the Vasy Supplier Bill List
(accounts payable). Bill-level, keyed by Bill No, with paid/due amounts, a
due date and a status (Paid / Due / Overdue) — so true AP aging (by due date)
is possible, unlike the expense-ledger proxy.

Real export columns (2026-07-10):
  Status · Bill No · Bill Date · Vendor · Amount · Paid Amount · Due Amount ·
  Tax Amount · Due Date · Created By

Upsert on bill_no — re-import refreshes paid/due/status as bills get settled.
Vendor is free-text (no supplier master). Shares the Bill No series with
VasyPurchase (same bills; this view adds the payment state).
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class VasySupplierBill(Base):
    __tablename__ = "vasy_supplier_bills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    bill_no: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    bill_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    vendor: Mapped[str] = mapped_column(String, nullable=False)              # supplier (raw)
    vendor_key: Mapped[str] = mapped_column(String, nullable=False, index=True)  # normalized
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    paid: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    due: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # open payable
    tax: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)  # paid|due|overdue
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
