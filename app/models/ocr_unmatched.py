"""
ocr_unmatched_lines — OCR lines from photographed dispatch/weight sheets
that could not be matched to a known product (illegible, unmatched alias,
missing quantity, etc).

Mirrors rate_unclear_queue's pattern: `resolved` is a Boolean (NOT a
nullable timestamp). Filter unresolved with:
    OcrUnmatchedLine.resolved == False
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OcrUnmatchedLine(Base):
    __tablename__ = "ocr_unmatched_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    raw_line: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)

    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_product: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    resolved_quantity: Mapped[Optional[float]] = mapped_column(Numeric(10, 3), nullable=True)
    resolved_unit: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
