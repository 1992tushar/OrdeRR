"""
test_onboarding.py
------------------
Integration tests for customer onboarding flow in order_service.py

Tests:
  - New customer first contact
  - Valid restaurant name
  - Invalid restaurant names (too short, greeting, number, filler)
  - Onboarding completion
"""

import pytest
from unittest.mock import patch


PHONE = "919800000020"


@pytest.fixture(autouse=True)
def mock_all_sends():
    with patch("app.services.notifier.send_whatsapp_message") as mock:
        mock.return_value = {"status": "mocked"}
        yield mock


class TestNewCustomer:

    def test_new_customer_creates_record(self, db):
        from app.services.order_service import process_incoming_order
        process_incoming_order(db, PHONE, "Hi")
        from app.services.customer_service import get_customer_by_phone
        customer = get_customer_by_phone(db, PHONE)
        assert customer is not None

    def test_new_customer_status_awaiting_name(self, db):
        from app.services.order_service import process_incoming_order
        process_incoming_order(db, PHONE, "Hi")
        from app.services.customer_service import get_customer_by_phone
        customer = get_customer_by_phone(db, PHONE)
        assert customer.onboarding_status == "awaiting_name"

    def test_new_customer_returns_awaiting_status(self, db):
        from app.services.order_service import process_incoming_order
        result = process_incoming_order(db, PHONE, "Hi")
        assert result["status"] == "awaiting_restaurant_name"
        assert result["order_id"] is None

    def test_new_customer_no_order_created(self, db):
        from app.services.order_service import process_incoming_order
        from app.models.order import Order
        process_incoming_order(db, PHONE, "Hi")
        orders = db.query(Order).filter(Order.customer_phone == PHONE).all()
        assert len(orders) == 0

    def test_welcome_message_sent(self, db):
        from app.services.order_service import process_incoming_order
        with patch("app.services.order_service.send_whatsapp_message") as mock:
            mock.return_value = {"status": "mocked"}
            process_incoming_order(db, PHONE, "Hi")
            assert mock.called
            msg = mock.call_args[0][1]
            assert "Welcome" in msg or "welcome" in msg


class TestOnboardingName:

    def test_valid_name_accepted(self, db, awaiting_customer):
        from app.services.order_service import process_incoming_order
        result = process_incoming_order(db, awaiting_customer.phone_number, "Hotel Sai Krupa")
        assert result["status"] == "customer_onboarded"

    def test_valid_name_sets_restaurant_name(self, db, awaiting_customer):
        from app.services.order_service import process_incoming_order
        from app.services.customer_service import get_customer_by_phone
        process_incoming_order(db, awaiting_customer.phone_number, "Hotel Sai Krupa")
        customer = get_customer_by_phone(db, awaiting_customer.phone_number)
        assert customer.restaurant_name == "Hotel Sai Krupa"

    def test_valid_name_sets_status_active(self, db, awaiting_customer):
        from app.services.order_service import process_incoming_order
        from app.services.customer_service import get_customer_by_phone
        process_incoming_order(db, awaiting_customer.phone_number, "Hotel Sai Krupa")
        customer = get_customer_by_phone(db, awaiting_customer.phone_number)
        assert customer.onboarding_status == "active"

    def test_too_short_name_rejected(self, db, awaiting_customer):
        from app.services.order_service import process_incoming_order
        result = process_incoming_order(db, awaiting_customer.phone_number, "AB")
        assert result["status"] == "invalid_restaurant_name"

    def test_too_short_name_keeps_awaiting(self, db, awaiting_customer):
        from app.services.order_service import process_incoming_order
        from app.services.customer_service import get_customer_by_phone
        process_incoming_order(db, awaiting_customer.phone_number, "AB")
        customer = get_customer_by_phone(db, awaiting_customer.phone_number)
        assert customer.onboarding_status == "awaiting_name"

    def test_greeting_as_name_rejected(self, db, awaiting_customer):
        from app.services.order_service import process_incoming_order
        result = process_incoming_order(db, awaiting_customer.phone_number, "Hi")
        assert result["status"] == "invalid_restaurant_name"

    def test_number_as_name_rejected(self, db, awaiting_customer):
        from app.services.order_service import process_incoming_order
        result = process_incoming_order(db, awaiting_customer.phone_number, "9876543210")
        assert result["status"] == "invalid_restaurant_name"

    def test_ok_as_name_rejected(self, db, awaiting_customer):
        from app.services.order_service import process_incoming_order
        result = process_incoming_order(db, awaiting_customer.phone_number, "ok")
        assert result["status"] == "invalid_restaurant_name"

    def test_valid_hindi_name_accepted(self, db, awaiting_customer):
        from app.services.order_service import process_incoming_order
        result = process_incoming_order(db, awaiting_customer.phone_number, "Sai Krupa Hotel")
        assert result["status"] == "customer_onboarded"