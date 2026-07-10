"""
customer_receipts — read-only mirror of a Vasy receipt (money in).

Vasy ERP is the source of truth for money; OrdeRR only mirrors it. One row per
Vasy receipt, keyed by the Vasy `Receipt No.` (PAY####) for idempotent upsert.

Modelled to the REAL receipt export columns (2026-07-10):
    # · Receipt No. · Party Name · Mode · Date · Amount · Status · Created By
`customer_id` is nullable — receipts to non-customers (Cash Customer, Gupta Gas,
walk-ins) stay unattributed rather than being dropped.
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class CustomerReceipt(Base):
    __tablename__ = "customer_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # Vasy Receipt No. (PAY####) — unique idempotency key for re-import
    receipt_no: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    party_name: Mapped[str] = mapped_column(String, nullable=False)          # raw from Vasy
    customer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=True, index=True       # NULL = unattributed
    )
    mode: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)   # 'bank' | 'cash' (normalized)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    receipt_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # e.g. 'cleared'
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
