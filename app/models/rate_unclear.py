"""
rate_unclear_queue — rate-message lines that could not be confidently
matched to a product via the alias pipeline. NEVER silently dropped.

This is a 6th billing-owned table, added beyond the original 5
(daily_rates, customer_rate_overrides, order_item_actuals, invoices,
invoice_items) because unresolved lines must survive process restarts
and be resolvable from a dashboard, not just held in memory.
"""
from datetime import date, datetime
from typing import Optional
from sqlalchemy import Integer, String, Date, DateTime, Text, Boolean, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class RateUnclearItem(Base):
    __tablename__ = "rate_unclear_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    raw_line: Mapped[str] = mapped_column(Text, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    resolved_product: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    resolved_rate: Mapped[Optional[float]] = mapped_column(Numeric(10, 4), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
