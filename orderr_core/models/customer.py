from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey
)

from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey

from orderr_core.database import Base


class Customer(Base):

    __tablename__ = "customers"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    restaurant_name = Column(
        String,
        nullable=True
    )

    owner_name = Column(
        String,
        nullable=True
    )

    phone_number = Column(
        String,
        unique=True,
        index=True,
        nullable=False
    )

    address = Column(
        String,
        nullable=True
    )

    city = Column(
        String,
        nullable=True
    )

    onboarding_status = Column(
        String,
        default="awaiting_name"
    )

    is_active = Column(
        Boolean,
        default=True
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # ── New fields for salesperson / pending order feature ──────────────

    area = Column(
        String,
        nullable=True
    )

    salesperson_id = Column(
        Integer,
        ForeignKey("salespersons.id"),
        nullable=True,
        index=True
    )

    # True  → expect a daily order; include in pending checks
    # False → irregular customer; never chase for missing orders
    is_daily_order_customer = Column(
        Boolean,
        default=True
    )

    ledger_token = Column(
    String,
    unique=True,
    nullable=True,
    index=True,
    )
    
    # Relationship
    salesperson = relationship("Salesperson", back_populates="customers")

