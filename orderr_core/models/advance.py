"""
Advance model — salary advances given to an employee, with repayments.
Owns the `advances` table; shares OrdeRR's Base/metadata.
"""
from datetime import datetime, timezone, timedelta

from sqlalchemy import Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base

_IST = timezone(timedelta(hours=5, minutes=30))


class Advance(Base):
    __tablename__ = "advances"

    id:            Mapped[int]   = mapped_column(Integer, primary_key=True)
    employee_id:   Mapped[int]   = mapped_column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    date:          Mapped[str]   = mapped_column(String, nullable=False)   # 'YYYY-MM-DD'
    amount:        Mapped[float] = mapped_column(Float, nullable=False)
    reason:        Mapped[str]   = mapped_column(String, nullable=True)
    repaid_amount: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    notes:         Mapped[str]   = mapped_column(String, nullable=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(_IST))
