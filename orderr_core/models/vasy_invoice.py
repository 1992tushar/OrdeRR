"""
vasy_invoices + vasy_invoice_items — read-only mirror of Vasy sales invoices
(authoritative revenue). Vasy = source of truth; OrdeRR only mirrors.

Modelled to the REAL Vasy sales-invoice export (2026-07-10), which is
line-item level:
    Sr.No · Date · Voucher No · Branch · Party Name · Mobile No. · Category ·
    Item Code · QTY · Net Amount · Sales Man · Receipt Data · Created By ·
    Address · Description · Note

One VasyInvoice per Voucher No (INV####); invoice total = Σ line net_amount.
Item Code maps to the ERP SKU catalog (erp_name resolved at import). Join to
OrdeRR customers by normalized party name (Mobile No. is usually blank).
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orderr_core.database import Base


class VasyInvoice(Base):
    __tablename__ = "vasy_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    voucher_no: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    invoice_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    party_name: Mapped[str] = mapped_column(String, nullable=False)          # raw from Vasy
    party_key: Mapped[str] = mapped_column(String, nullable=False, index=True)  # normalized name
    customer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=True, index=True       # NULL = unmatched
    )
    total: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # Σ line net
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    branch: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    items: Mapped[list["VasyInvoiceItem"]] = relationship(
        "VasyInvoiceItem", back_populates="invoice", cascade="all, delete-orphan"
    )


class VasyInvoiceItem(Base):
    __tablename__ = "vasy_invoice_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vasy_invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vasy_invoices.id"), nullable=False, index=True
    )
    item_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    erp_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)   # resolved SKU display name
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    qty: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    net_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    invoice: Mapped["VasyInvoice"] = relationship("VasyInvoice", back_populates="items")
