from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.sql import func
from app.database import Base


class InboundMessage(Base):
    __tablename__ = "inbound_messages"

    id                       = Column(Integer, primary_key=True, index=True)
    meta_message_id          = Column(String, unique=True, index=True, nullable=True)
    customer_phone           = Column(String, index=True, nullable=False)
    raw_message              = Column(Text, nullable=True)
    payload_json             = Column(Text, nullable=True)
    message_type             = Column(String, nullable=True)
    received_at              = Column(DateTime(timezone=True), server_default=func.now())
    processing_status        = Column(String, default="RECEIVED", index=True)
    processing_attempts      = Column(Integer, default=0)
    last_retry_at            = Column(DateTime(timezone=True), nullable=True)
    failure_reason           = Column(Text, nullable=True)
    parser_confidence        = Column(String, nullable=True)
    linked_order_id          = Column(Integer, nullable=True)
    acknowledged_to_customer = Column(Boolean, default=False)
    ack_attempts             = Column(Integer, default=0)
    ack_failed               = Column(Boolean, default=False)
    is_duplicate             = Column(Boolean, default=False)
    created_at               = Column(DateTime(timezone=True), server_default=func.now())
    updated_at               = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<InboundMessage id={self.id} status={self.processing_status} phone={self.customer_phone}>"
