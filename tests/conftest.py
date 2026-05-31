"""
conftest.py
-----------
Shared fixtures for all OrdeRR tests.
Uses in-memory SQLite DB — no Render/PostgreSQL needed.
All WhatsApp sends are mocked — no real messages sent.
"""

import pytest
import os
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = "sqlite:///:memory:"

os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("PLANT_NAME", "Fluffy")
os.environ.setdefault("MANAGER_PHONE", "919999999999")
os.environ.setdefault("DISPATCH_CUTOFF_HOUR", "9")


@pytest.fixture(scope="function")
def db():
    """Fresh in-memory SQLite DB for every test."""
    from app.database import Base
    from app.models.salesperson import Salesperson
    from app.models.customer import Customer
    from app.models.order import Order
    from app.models.inbound_message import InboundMessage

    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestSession()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def mock_whatsapp():
    """Mock all WhatsApp sends — no real messages sent during tests."""
    with patch("app.services.notifier.send_whatsapp_message") as mock:
        mock.return_value = {"status": "mocked"}
        yield mock


@pytest.fixture
def registered_customer(db):
    """Fully onboarded active customer with salesperson assigned."""
    from app.models.customer import Customer
    from app.models.salesperson import Salesperson

    sp = Salesperson(name="Test SP", phone="917700000000", active=True)
    db.add(sp)
    db.commit()

    customer = Customer(
        phone_number="919800000001",
        restaurant_name="Test Hotel",
        onboarding_status="active",
        is_active=True,
        is_daily_order_customer=True,
        salesperson_id=sp.id,
        area="Talegaon",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@pytest.fixture
def awaiting_customer(db):
    """Customer who registered but hasn't provided restaurant name yet."""
    from app.models.customer import Customer
    customer = Customer(
        phone_number="919800000002",
        restaurant_name=None,
        onboarding_status="awaiting_name",
        is_active=True,
        is_daily_order_customer=True,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer
