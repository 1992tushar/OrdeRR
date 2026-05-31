"""
test_customer_service.py
------------------------
Unit tests for customer_service.py

Tests:
  - normalize_phone()
  - get_customer_by_phone()
  - create_new_customer()
"""

import pytest
from app.services.customer_service import normalize_phone, get_customer_by_phone, create_new_customer


class TestNormalizePhone:
    def test_10_digit_adds_91(self):
        assert normalize_phone("9876543210") == "919876543210"

    def test_already_has_91(self):
        assert normalize_phone("919876543210") == "919876543210"

    def test_strips_plus(self):
        assert normalize_phone("+919876543210") == "919876543210"

    def test_strips_spaces(self):
        assert normalize_phone("98765 43210") == "919876543210"

    def test_strips_dashes(self):
        assert normalize_phone("98765-43210") == "919876543210"

    def test_no_double_91_prefix(self):
        result = normalize_phone("919876543210")
        assert result.count("91") == 1 or result == "919876543210"


class TestGetCustomerByPhone:
    def test_returns_customer_when_exists(self, db, registered_customer):
        result = get_customer_by_phone(db, "919800000001")
        assert result is not None
        assert result.restaurant_name == "Test Hotel"

    def test_returns_none_when_not_exists(self, db):
        result = get_customer_by_phone(db, "919800000099")
        assert result is None

    def test_normalizes_phone_before_lookup(self, db, registered_customer):
        # 10-digit should still find the customer
        result = get_customer_by_phone(db, "9800000001")
        assert result is not None


class TestCreateNewCustomer:
    def test_creates_customer(self, db):
        customer = create_new_customer(db, "919800000010")
        assert customer is not None
        assert customer.phone_number == "919800000010"

    def test_onboarding_status_awaiting_name(self, db):
        customer = create_new_customer(db, "919800000011")
        assert customer.onboarding_status == "awaiting_name"

    def test_restaurant_name_is_none(self, db):
        customer = create_new_customer(db, "919800000012")
        assert customer.restaurant_name is None

    def test_normalizes_phone(self, db):
        customer = create_new_customer(db, "9800000013")
        assert customer.phone_number == "919800000013"
