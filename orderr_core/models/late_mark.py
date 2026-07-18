"""
LateMark model — a late-arrival mark for an employee. Each late mark records the
arrival time and an optional note, and levies a FLAT fine (config.LATE_MARK_FINE,
default ₹200) that is deducted from that pay cycle's salary — unlike leaves,
which deduct a per-day rate. Owns the `late_marks` table; shares OrdeRR's
Base/metadata.
"""
from datetime import datetime

from sqlalchemy import Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base
from orderr_core.constants import IST as _IST


class LateMark(Base):
    __tablename__ = "late_marks"

    id:          Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    date:        Mapped[str] = mapped_column(String, nullable=False)   # 'YYYY-MM-DD'
    time:        Mapped[str] = mapped_column(String, nullable=True)    # 'HH:MM' arrival time
    note:        Mapped[str] = mapped_column(String, nullable=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(_IST))
