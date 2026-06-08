from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.sql import func

from app.database import Base


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)

    plant_name     = Column(String, default="Fluffy")
    customer_phone = Column(String, index=True)
    customer_name  = Column(String, nullable=True)

    raw_message    = Column(Text)
    is_photo_order = Column(Boolean, default=False)

    parsed_items  = Column(Text, nullable=True)

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

    # Stores JSON array strings of unmapped items needing dashboard evaluation
    unclear_items  = Column(Text, nullable=True)

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