from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from orderr_core.database import Base

from orderr_core.constants import IST


class CustomerAlias(Base):
    """Maps an alternate normalized party name → a canonical customer.

    Solves the Vasy split-customer problem: the sales-invoice export sometimes
    names a party differently from the customer master (e.g. "PATILVADA" vs
    "PATILVADA HOTEL") and carries no mobile, so the importer can't link those
    invoices to the right customer and files them under an auto-created twin.
    An alias records `alias_key = normalize("PATILVADA") → customer_id(PATILVADA
    HOTEL)`; every Vasy importer consults it, so future imports auto-link and the
    two never split again. Seeded (approval-based) from the Data-health screen.
    """
    __tablename__ = "customer_aliases"

    id          = Column(Integer, primary_key=True, index=True)
    alias_key   = Column(String, nullable=False, unique=True, index=True)  # normalized name
    customer_id = Column(Integer, index=True, nullable=False)              # canonical target
    source      = Column(String, nullable=True)                           # e.g. "data-health-merge"
    created_at  = Column(DateTime, default=lambda: datetime.now(IST))

    __table_args__ = (
        UniqueConstraint("alias_key", name="uq_customer_alias_key"),
    )

    def __repr__(self):
        return f"<CustomerAlias '{self.alias_key}' → customer #{self.customer_id}>"
