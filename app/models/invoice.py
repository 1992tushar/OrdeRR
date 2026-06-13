from sqlalchemy import Column, Integer, String, Date, DateTime, Numeric, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class Invoice(Base):
    __tablename__ = "invoices"

    id                = Column(Integer, primary_key=True, index=True)
    invoice_number    = Column(Integer, unique=True, nullable=False, index=True)
    order_id          = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    customer_phone    = Column(String, nullable=False, index=True)
    customer_name     = Column(String, nullable=True)
    invoice_date      = Column(Date, nullable=False)
    line_items        = Column(JSONB, nullable=False)
    subtotal          = Column(Numeric(10, 3), nullable=True)
    additional_charge = Column(Numeric(10, 3), default=0, nullable=True)
    round_off         = Column(Numeric(10, 3), default=0, nullable=True)
    total_amount      = Column(Numeric(10, 3), nullable=True)
    due_amount        = Column(Numeric(10, 3), nullable=True)
    pdf_path          = Column(String, nullable=True)
    status            = Column(String, default="generated", nullable=False)
    generated_at      = Column(DateTime(timezone=True), server_default=func.now())
    generated_by      = Column(String, default="manual", nullable=False)

    def __repr__(self):
        return (
            f"<Invoice id={self.id} "
            f"invoice_number={self.invoice_number} "
            f"order_id={self.order_id} "
            f"status={self.status}>"
        )


class CustomerProductPrice(Base):
    __tablename__ = "customer_product_prices"

    id             = Column(Integer, primary_key=True, index=True)
    customer_phone = Column(String, nullable=False, index=True)
    product_name   = Column(String, nullable=False)
    price_per_unit = Column(Numeric(10, 2), nullable=False)
    uom            = Column(String, default="KGS")
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("customer_phone", "product_name", name="uq_customer_product_price"),
    )

    def __repr__(self):
        return (
            f"<CustomerProductPrice "
            f"customer={self.customer_phone} "
            f"product={self.product_name} "
            f"price={self.price_per_unit}>"
        )


class DefaultProductPrice(Base):
    __tablename__ = "default_product_prices"

    id             = Column(Integer, primary_key=True, index=True)
    product_name   = Column(String, unique=True, nullable=False, index=True)
    price_per_unit = Column(Numeric(10, 2), nullable=False)
    uom            = Column(String, default="KGS")
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return (
            f"<DefaultProductPrice "
            f"product={self.product_name} "
            f"price={self.price_per_unit}>"
        )


class ProductItemCode(Base):
    __tablename__ = "product_item_codes"

    id           = Column(Integer, primary_key=True, index=True)
    product_name = Column(String, unique=True, nullable=False, index=True)
    item_code    = Column(String, nullable=False)
    uom          = Column(String, default="KGS")

    def __repr__(self):
        return (
            f"<ProductItemCode "
            f"product={self.product_name} "
            f"item_code={self.item_code}>"
        )