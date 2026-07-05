from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from orderr_core.database import Base

IST = timezone(timedelta(hours=5, minutes=30))


class CustomerProductAlias(Base):
    __tablename__ = "customer_product_aliases"

    id                     = Column(Integer, primary_key=True, index=True)
    customer_phone         = Column(String, index=True, nullable=False)
    raw_text               = Column(String, nullable=False)
    canonical_product_name = Column(String, nullable=False)
    created_at             = Column(DateTime, default=lambda: datetime.now(IST))
    updated_at             = Column(DateTime, default=lambda: datetime.now(IST),
                                    onupdate=lambda: datetime.now(IST))

    __table_args__ = (
        UniqueConstraint("customer_phone", "raw_text", name="uq_customer_raw_text"),
    )

    def __repr__(self):
        return (
            f"<CustomerProductAlias {self.customer_phone}: "
            f"'{self.raw_text}' → '{self.canonical_product_name}'>"
        )

