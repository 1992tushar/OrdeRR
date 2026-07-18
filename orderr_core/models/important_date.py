"""
important_dates — renewals & recurring maintenance (Registers & Reminders spec
§4): insurance, vehicle servicing, licenses, AMCs…

Enters the attention feed `lead_days` before `due_date`, turns overdue after
it, and nags daily until marked done. Mark-done advances recurring items by
the item's advance rule:
  anniversary — fixed schedule regardless of when you acted (insurance,
                licenses: the policy date doesn't move because you paid late)
  from_done   — next due counts from the day you actually did it (servicing)
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class ImportantDate(Base):
    __tablename__ = "important_dates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False, default="other")
    # insurance | vehicle_service | license | amc | subscription | other
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    recurrence: Mapped[str] = mapped_column(String(10), nullable=False, default="none")
    # none | days | monthly | quarterly | yearly
    recur_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # for recurrence='days'
    advance_rule: Mapped[str] = mapped_column(String(12), nullable=False, default="anniversary")
    # anniversary | from_done
    lead_days: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    linked_to: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # which vehicle/policy/asset
    amount_estimate: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active",
                                        index=True)  # active | paused | done (done = one-off completed)
    last_done_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
