from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from app.database import Base

class OrderSession(Base):
    __tablename__ = "order_sessions"

    id         = Column(Integer, primary_key=True, index=True)
    phone      = Column(String, unique=True, index=True, nullable=False)
    step       = Column(String, nullable=False)  # selecting_item | awaiting_qty | confirming
    items_json = Column(Text, default="[]")       # accumulated items so far
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())