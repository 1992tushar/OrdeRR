"""
import_logs — audit of each nightly/manual Vasy import (one framework, many
entities). Records what file was imported, how many rows, and how many matched
vs stayed unattributed, so imports are traceable.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class ImportLog(Base):
    __tablename__ = "import_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    entity: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # 'receipts' | 'outstanding'
    source_file: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unmatched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
