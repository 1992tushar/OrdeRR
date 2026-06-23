from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, JSON
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)

    plant_name     = Column(String, default="Fluffy")
    customer_phone = Column(String, index=True)
    customer_name  = Column(String, nullable=True)

    raw_message    = Column(Text)
    is_photo_order = Column(Boolean, default=False)

    # JSON on SQLite (local), JSONB on Postgres (Render) — same code, both environments
    parsed_items  = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    unclear_items = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)

    # delivery_date always set at order creation as YYYY-MM-DD string
    delivery_date = Column(String, nullable=True, index=True)
    delivery_time = Column(String, nullable=True)

    # status: received → confirmed → packed → delivered → cancelled
    status = Column(String, default="received")

    # soft cancel — keeps record in DB for history
    is_cancelled = Column(Boolean, default=False)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    is_unclear     = Column(Boolean, default=False)
    unclear_reason = Column(String, nullable=True)

    business_date        = Column(String, nullable=True, index=True)
    is_next_day_override = Column(Boolean, default=False)

    confirmation_sent    = Column(Boolean, default=False)
    forwarded_to_manager = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )

    def __repr__(self):
        return (
            f"<Order id={self.id} "
            f"customer={self.customer_phone} "
            f"status={self.status}>"
        )