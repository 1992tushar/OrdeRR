"""
customer_rate_overrides — negotiated rates for specific customer+product.
Takes priority over daily_rates when active:
  effective_from <= business_date AND (effective_to IS NULL OR effective_to >= business_date)
"""
from datetime import date, datetime
from typing import Optional
from sqlalchemy import Integer, String, Numeric, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class CustomerRateOverride(Base):
    __tablename__ = "customer_rate_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    customer_phone: Mapped[str] = mapped_column(String, nullable=False, index=True)
    product: Mapped[str] = mapped_column(String, nullable=False, index=True)
    rate_per_unit: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)   # 'kg' | 'nos'
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # null = still active
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
