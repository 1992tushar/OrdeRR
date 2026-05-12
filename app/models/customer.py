from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime
)

from sqlalchemy.sql import func

from app.database import Base


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