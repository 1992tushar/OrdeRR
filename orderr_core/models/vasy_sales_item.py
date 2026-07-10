"""
vasy_sales_items — read-only mirror of Vasy's "Sales and Sales Return Item
Register" (/report/salesandsalesreturnitemregister). Vasy = source of truth.

Unlike the header-level sales-invoice export (one row per invoice, no SKU
detail), this report is LINE-ITEM level and carries per-SKU quantity, value AND
Landing Cost (per-unit cost of goods) — so it powers billed product-mix and,
later, true per-SKU / per-customer gross margin.

Columns in the export (2026-07):
    Sr.No · Date · Voucher No · Sale Type · Party Name · Product Name ·
    Item Code · Batch No · landing Cost · MRP · QTY · Taxable Amount ·
    Discount · Other Discount · Tax Amount · Net Amount · State Name ·
    Total MRP · Coupon Discount · Coupon Discount Tax Inclusive

Sale Type is "Invoice" or "Sales Return" (returns net down mix/revenue). Joined
to OrdeRR customers by normalized party name. Snapshot-replace on each import
(line items have no stable document key; the bot re-exports the full FY).
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class VasySalesItem(Base):
    __tablename__ = "vasy_sales_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    invoice_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    voucher_no: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    sale_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # Invoice / Sales Return
    party_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    party_key: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    customer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=True, index=True    # NULL = unmatched
    )
    product_name: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    item_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    batch_no: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    landing_cost: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)  # per unit
    mrp: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    qty: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False, default=0)
    taxable_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    discount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    net_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    state: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
