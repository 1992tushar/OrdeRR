"""
daily_rates — one row per product per business date.
Rate lookup uses most recent row for product+date.
"""
from datetime import date, datetime
from sqlalchemy import Integer, String, Numeric, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from orderr_core.database import Base


class DailyRate(Base):
    __tablename__ = "daily_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    product: Mapped[str] = mapped_column(String, nullable=False, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    rate_per_unit: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)   # 'kg' | 'nos'
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # 'whatsapp' | 'dashboard'
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
