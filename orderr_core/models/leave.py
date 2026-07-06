"""
Leave model — full-day / half-day leave entries per employee.
Owns the `leaves` table; shares OrdeRR's Base/metadata.
"""
from datetime import datetime, timezone, timedelta

from sqlalchemy import Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base

_IST = timezone(timedelta(hours=5, minutes=30))


class Leave(Base):
    __tablename__ = "leaves"

    id:          Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    date:        Mapped[str] = mapped_column(String, nullable=False)   # 'YYYY-MM-DD'
    type:        Mapped[str] = mapped_column(String, nullable=False)   # 'full' | 'half'
    # Complementary (employer-granted) leave: recorded and shown, but never
    # deducted from salary. Defaults to False so ordinary leaves are chargeable.
    paid:        Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason:      Mapped[str] = mapped_column(String, nullable=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(_IST))
