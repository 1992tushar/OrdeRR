"""
AdvanceRepayment model — dated ledger of repayments against an advance.

Each row is one recovery instalment (a variable amount, decided per month by
what the employee can pay). Advance.repaid_amount is kept as the running total
of these rows so existing outstanding math is unchanged; this table adds the
per-payment history and lets the payslip attribute "recovered this month".
Owns the `advance_repayments` table; shares OrdeRR's Base/metadata.
"""
from datetime import datetime, timezone, timedelta

from sqlalchemy import Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base

from orderr_core.constants import IST as _IST


class AdvanceRepayment(Base):
    __tablename__ = "advance_repayments"

    id:          Mapped[int] = mapped_column(Integer, primary_key=True)
    advance_id:  Mapped[int] = mapped_column(Integer, ForeignKey("advances.id"), nullable=False, index=True)
    employee_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    date:        Mapped[str] = mapped_column(String, nullable=False)   # 'YYYY-MM-DD'
    amount:      Mapped[float] = mapped_column(Float, nullable=False)
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(_IST))
