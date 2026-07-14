"""
broadcast_recipients — the owner-curated list of daily-order customers.
One row per customer. This list is the single roster for:

  - the 📣 Broadcast screen's manual order reminder (replaced the 22:00
    auto-reminder, removed 2026-07-14),
  - the public live status page /r/<REPORT_LINK_KEY> (who ordered vs pending),
  - pending_orders.active_daily_customers_q — so the 23:15 salesperson nudge
    and ad-hoc pending replies too (replaced the never-populated
    is_daily_order_customer flag, owner decision 2026-07-14).
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class BroadcastRecipient(Base):
    __tablename__ = "broadcast_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=False, unique=True, index=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # when the reminder template was last sent to this member (NULL = never)
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
