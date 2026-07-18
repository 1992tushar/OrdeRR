"""
wa_status_events — outbound WhatsApp delivery-status journal.

Meta POSTs a `statuses` callback for every outbound message (sent → delivered
→ read, or failed with an error). The webhook used to discard these, which
made send failures invisible: the Graph API answers "accepted" even for
messages it later drops (unregistered number, empty prepaid balance, …).
One row per callback; `errors_json` holds Meta's error array verbatim.
"""
from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from orderr_core.database import Base


class WaStatusEvent(Base):
    __tablename__ = "wa_status_events"

    id              = Column(Integer, primary_key=True, index=True)
    meta_message_id = Column(String, index=True, nullable=True)   # wamid of the outbound message
    recipient_phone = Column(String, index=True, nullable=True)
    status          = Column(String, index=True, nullable=False)  # sent | delivered | read | failed
    error_code      = Column(Integer, nullable=True)              # first error code when failed
    error_title     = Column(String, nullable=True)
    error_detail    = Column(Text, nullable=True)
    errors_json     = Column(Text, nullable=True)                 # full errors array, verbatim
    occurred_at     = Column(DateTime(timezone=True), nullable=True)  # Meta's timestamp
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<WaStatusEvent id={self.id} status={self.status} phone={self.recipient_phone} code={self.error_code}>"
