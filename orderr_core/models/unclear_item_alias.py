"""
unclear_item_alias.py
---------------------
Stores manager-confirmed mappings from raw unparsed text → canonical product name.
Used by template_parser.py to auto-resolve previously unclear items.
"""

from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, Integer, String, DateTime
from orderr_core.database import Base

from orderr_core.constants import IST


class UnclearItemAlias(Base):
    __tablename__ = "unclear_item_aliases"

    id                   = Column(Integer, primary_key=True, index=True)
    raw_text             = Column(String, unique=True, nullable=False, index=True)
    canonical_product_name = Column(String, nullable=False)
    created_at           = Column(DateTime, default=lambda: datetime.now(IST))
    updated_at           = Column(DateTime, default=lambda: datetime.now(IST),
                                  onupdate=lambda: datetime.now(IST))

