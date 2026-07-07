"""
invoices + invoice_items

Key rules:
- One invoice per order (unique constraint on order_id).
- rate_used is SNAPSHOTTED at generation time — never recalculated retroactively.
- amount = qty × rate_used (no rounding, keep paise).
- Invoice number format: FLUFFY-YYYYMMDD-NNN (zero-padded 3-digit sequence per day).
- status: 'draft' | 'sent' | 'paid' | 'partial' | 'void'
- rate_source: 'daily_rate' | 'customer_override' | 'carried_forward_rate'
"""
from datetime import date, datetime
from typing import Optional
from sqlalchemy import Integer, String, Numeric, Date, DateTime, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from orderr_core.database import Base
from sqlalchemy import Integer, String, Numeric, Date, DateTime, func, UniqueConstraint, ForeignKey

class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    invoice_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    # unique=True enforces one invoice per order (idempotency §5.3b)
    customer_phone: Mapped[str] = mapped_column(String, nullable=False, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    subtotal: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    total: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    items: Mapped[list["InvoiceItem"]] = relationship(
        "InvoiceItem", back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    invoice_id: Mapped[int] = mapped_column(
    Integer, ForeignKey("invoices.id"), nullable=False, index=True
    )
    product: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)        # 'kg' | 'nos'
    rate_used: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)  # snapshotted, never changes
    amount: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)     # qty × rate_used
    rate_source: Mapped[str] = mapped_column(String(30), nullable=False)      # 'daily_rate' | 'customer_override' | 'carried_forward_rate'

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="items")
