"""
broadcast_recipients — the owner-curated WhatsApp broadcast list for order
reminders. Replaces the old 22:00 auto-reminder to every pending customer
(removed 2026-07-14): the owner adds/removes customers here and sends the
`customer_order_reminder_v2` Meta template on demand from the 📣 Broadcast
screen. One row per customer; membership only, the message itself is the
approved Meta template.
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
