"""
analytics_prefs — per-user Analytics UI preferences. Currently just the
pinned/bookmarked subnav tabs, stored server-side so pins sync across the
owner's devices (localStorage was per-device). One row per user; auth is
currently disabled so every request is user "anonymous" → effectively one
shared pin list for the plant, which is the intent.

`pinned_tabs` is a JSON array of tab-key slugs (e.g. ["overview","chase"]),
kept in the order the user pinned them.
"""
from datetime import datetime

from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class AnalyticsPref(Base):
    __tablename__ = "analytics_prefs"

    username: Mapped[str] = mapped_column(String(80), primary_key=True)
    pinned_tabs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
