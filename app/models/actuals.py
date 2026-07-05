"""
order_item_actuals — physically weighed/dispatched quantities.
This is what gets billed — NEVER the ordered quantity.

capture_source: 'photo_ocr' | 'dashboard_manual'
confidence:     'auto' (legible) | 'needs_review' (illegible/missing)

Confidence = legibility ONLY. Never compare actual vs ordered qty.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, String, Numeric, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class OrderItemActual(Base):
    __tablename__ = "order_item_actuals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # order_id FK → orderr_core orders.id (billing reads, never writes OrdeRR tables)
    product: Mapped[str] = mapped_column(String, nullable=False)
    ordered_quantity: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    ordered_unit: Mapped[str] = mapped_column(String(10), nullable=False)
    actual_quantity: Mapped[Optional[float]] = mapped_column(Numeric(10, 3), nullable=True)
    actual_unit: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    capture_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 'photo_ocr' | 'dashboard_manual'
    confidence: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)      # 'auto' | 'needs_review'
    confirmed_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
