"""
vasy_purchases + vasy_purchase_items — read-only mirror of Vasy purchase bills
(COGS input). Line-item level export, grouped by Bill No. Supplier party is
free-text (no supplier master), so party_name is stored raw (+ normalized key)
with no FK. Bill total = Σ line amount.

Real export columns (2026-07-10):
  Sr No · Bill Date · Bill No · Voucher No · Party Name · GST No · Pan No ·
  HSN · Product Name · Item Code · Rate · QTY · Total Amount · Location ·
  Total Bill Amount · Created By
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orderr_core.database import Base


class VasyPurchase(Base):
    __tablename__ = "vasy_purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    bill_no: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    bill_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    party_name: Mapped[str] = mapped_column(String, nullable=False)          # supplier (raw)
    party_key: Mapped[str] = mapped_column(String, nullable=False, index=True)  # normalized
    total: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # Σ line amount
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    items: Mapped[list["VasyPurchaseItem"]] = relationship(
        "VasyPurchaseItem", back_populates="purchase", cascade="all, delete-orphan"
    )


class VasyPurchaseItem(Base):
    __tablename__ = "vasy_purchase_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vasy_purchase_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vasy_purchases.id"), nullable=False, index=True
    )
    product_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    item_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    hsn: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    rate: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    qty: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    purchase: Mapped["VasyPurchase"] = relationship("VasyPurchase", back_populates="items")
