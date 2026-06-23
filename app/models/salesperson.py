from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class Salesperson(Base):

    __tablename__ = "salespersons"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=False)

    phone = Column(
        String,
        nullable=False,
        unique=True,
        index=True
    )

    area = Column(String, nullable=True)

    active = Column(Boolean, default=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    customers = relationship("Customer", back_populates="salesperson")

    def __repr__(self):
        return f"<Salesperson id={self.id} name={self.name} phone={self.phone} area={self.area}>"