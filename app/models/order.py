from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.sql import func
from app.database import Base

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    
    # Customer details
    customer_phone = Column(String, index=True)
    customer_name = Column(String, nullable=True)
    
    # Raw message from WhatsApp
    raw_message = Column(Text)
    is_photo_order = Column(Boolean, default=False)
    
    # Parsed order details
    parsed_items = Column(Text, nullable=True)  # JSON string
    delivery_time = Column(String, nullable=True)
    delivery_date = Column(String, nullable=True)
    
    # Status tracking
    status = Column(String, default="received")
    # received → confirmed → packed → delivered
    
    # Flags
    is_unclear = Column(Boolean, default=False)
    unclear_reason = Column(String, nullable=True)
    
    # Confirmation sent back to customer
    confirmation_sent = Column(Boolean, default=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<Order id={self.id} customer={self.customer_phone} status={self.status}>"