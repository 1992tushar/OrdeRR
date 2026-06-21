"""
app/models/customer_product_stats.py

One row per (customer_phone, product) pair.
Maintained incrementally — the unit-inference hot path reads this single
indexed row instead of scanning raw order history.

See FRD §5.1 for the full data-model rationale.
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, Integer, String, Float, DateTime, UniqueConstraint

from app.database import Base

IST = timezone(timedelta(hours=5, minutes=30))


class CustomerProductStats(Base):
    __tablename__ = "customer_product_stats"

    id             = Column(Integer, primary_key=True, index=True)
    customer_phone = Column(String, nullable=False, index=True)
    product        = Column(String, nullable=False)          # canonical display name
    order_count    = Column(Integer, default=0, nullable=False)
    avg_qty_kg     = Column(Float,   nullable=True)          # None until first sample
    min_qty_kg     = Column(Float,   nullable=True)
    max_qty_kg     = Column(Float,   nullable=True)
    updated_at     = Column(
        DateTime,
        default=lambda: datetime.now(IST),
        onupdate=lambda: datetime.now(IST),
    )

    # Single indexed lookup on the hot path — mirrors CustomerProductAlias pattern
    __table_args__ = (
        UniqueConstraint("customer_phone", "product", name="uq_cps_phone_product"),
    )

    def __repr__(self) -> str:
        return (
            f"<CustomerProductStats phone={self.customer_phone!r} "
            f"product={self.product!r} n={self.order_count} "
            f"range=[{self.min_qty_kg}, {self.max_qty_kg}] kg>"
        )
