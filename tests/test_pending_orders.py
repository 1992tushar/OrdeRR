"""
test_pending_orders.py
----------------------
Integration tests for pending_orders.py

Tests:
  - get_delivery_date_for_now() always returns today
  - get_pending_customers() correctly identifies pending vs ordered
  - Grouping by salesperson
  - Inactive / non-daily customers excluded
"""

import pytest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta, date

IST = timezone(timedelta(hours=5, minutes=30))
TODAY = datetime.now(IST).strftime("%Y-%m-%d")


class TestDeliveryDate:

    def test_returns_today(self):
        from app.services.pending_orders import get_delivery_date_for_now
        result = get_delivery_date_for_now()
        assert result == datetime.now(IST).date()

    def test_never_returns_tomorrow(self):
        """Even at 11:59 PM it should return today."""
        from app.services.pending_orders import get_delivery_date_for_now
        with patch("app.services.pending_orders.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 31, 23, 59, 0, tzinfo=IST)
            result = get_delivery_date_for_now()
        assert result == date(2026, 5, 31)


class TestGetPendingCustomers:

    def test_customer_with_no_order_is_pending(self, db):
        from app.models.customer import Customer
        from app.models.salesperson import Salesperson
        from app.services.pending_orders import get_pending_customers

        sp = Salesperson(name="SP1", phone="917700000001", active=True)
        db.add(sp)
        db.commit()

        customer = Customer(
            phone_number="919800000040",
            restaurant_name="Hotel A",
            onboarding_status="active",
            is_active=True,
            is_daily_order_customer=True,
            salesperson_id=sp.id,
        )
        db.add(customer)
        db.commit()

        grouped = get_pending_customers(db, datetime.now(IST).date())
        all_pending = [c for customers in grouped.values() for c in customers]
        phones = [c.phone_number for c in all_pending]
        assert "919800000040" in phones

    def test_customer_with_order_not_pending(self, db):
        from app.models.customer import Customer
        from app.models.order import Order
        from app.models.salesperson import Salesperson
        from app.services.pending_orders import get_pending_customers

        sp = Salesperson(name="SP2", phone="917700000002", active=True)
        db.add(sp)
        db.commit()

        customer = Customer(
            phone_number="919800000041",
            restaurant_name="Hotel B",
            onboarding_status="active",
            is_active=True,
            is_daily_order_customer=True,
            salesperson_id=sp.id,
        )
        db.add(customer)
        db.commit()

        order = Order(
            customer_phone="919800000041",
            customer_name="Hotel B",
            raw_message="Wings - 2 kg",
            delivery_date=TODAY,
            is_cancelled=False,
            status="received",
        )
        db.add(order)
        db.commit()

        grouped = get_pending_customers(db, datetime.now(IST).date())
        all_pending = [c for customers in grouped.values() for c in customers]
        phones = [c.phone_number for c in all_pending]
        assert "919800000041" not in phones

    def test_inactive_customer_excluded(self, db):
        from app.models.customer import Customer
        from app.models.salesperson import Salesperson
        from app.services.pending_orders import get_pending_customers

        sp = Salesperson(name="SP3", phone="917700000003", active=True)
        db.add(sp)
        db.commit()

        customer = Customer(
            phone_number="919800000042",
            restaurant_name="Hotel C",
            onboarding_status="active",
            is_active=False,   # ← inactive
            is_daily_order_customer=True,
            salesperson_id=sp.id,
        )
        db.add(customer)
        db.commit()

        grouped = get_pending_customers(db, datetime.now(IST).date())
        all_pending = [c for customers in grouped.values() for c in customers]
        phones = [c.phone_number for c in all_pending]
        assert "919800000042" not in phones

    def test_non_daily_customer_excluded(self, db):
        from app.models.customer import Customer
        from app.models.salesperson import Salesperson
        from app.services.pending_orders import get_pending_customers

        sp = Salesperson(name="SP4", phone="917700000004", active=True)
        db.add(sp)
        db.commit()

        customer = Customer(
            phone_number="919800000043",
            restaurant_name="Hotel D",
            onboarding_status="active",
            is_active=True,
            is_daily_order_customer=False,  # ← not daily
            salesperson_id=sp.id,
        )
        db.add(customer)
        db.commit()

        grouped = get_pending_customers(db, datetime.now(IST).date())
        all_pending = [c for customers in grouped.values() for c in customers]
        phones = [c.phone_number for c in all_pending]
        assert "919800000043" not in phones

    def test_awaiting_name_customer_excluded(self, db):
        from app.models.customer import Customer
        from app.services.pending_orders import get_pending_customers

        customer = Customer(
            phone_number="919800000044",
            restaurant_name=None,
            onboarding_status="awaiting_name",  # ← not onboarded
            is_active=True,
            is_daily_order_customer=True,
        )
        db.add(customer)
        db.commit()

        grouped = get_pending_customers(db, datetime.now(IST).date())
        all_pending = [c for customers in grouped.values() for c in customers]
        phones = [c.phone_number for c in all_pending]
        assert "919800000044" not in phones

    def test_grouped_by_salesperson(self, db):
        from app.models.customer import Customer
        from app.models.salesperson import Salesperson
        from app.services.pending_orders import get_pending_customers

        sp = Salesperson(name="SP5", phone="917700000005", active=True)
        db.add(sp)
        db.commit()

        for i, name in enumerate(["Hotel X", "Hotel Y"]):
            c = Customer(
                phone_number=f"91980000005{i}",
                restaurant_name=name,
                onboarding_status="active",
                is_active=True,
                is_daily_order_customer=True,
                salesperson_id=sp.id,
            )
            db.add(c)
        db.commit()

        grouped = get_pending_customers(db, datetime.now(IST).date())
        assert sp.id in grouped
        assert len(grouped[sp.id]) == 2

    def test_cancelled_order_still_pending(self, db):
        """A customer whose only order was cancelled should still appear as pending."""
        from app.models.customer import Customer
        from app.models.order import Order
        from app.models.salesperson import Salesperson
        from app.services.pending_orders import get_pending_customers

        sp = Salesperson(name="SP6", phone="917700000006", active=True)
        db.add(sp)
        db.commit()

        customer = Customer(
            phone_number="919800000060",
            restaurant_name="Hotel E",
            onboarding_status="active",
            is_active=True,
            is_daily_order_customer=True,
            salesperson_id=sp.id,
        )
        db.add(customer)
        db.commit()

        order = Order(
            customer_phone="919800000060",
            customer_name="Hotel E",
            raw_message="Wings - 2 kg",
            delivery_date=TODAY,
            is_cancelled=True,
            status="cancelled",
        )
        db.add(order)
        db.commit()

        grouped = get_pending_customers(db, datetime.now(IST).date())
        all_pending = [c for customers in grouped.values() for c in customers]
        phones = [c.phone_number for c in all_pending]
        # After fix: cancelled order does not count as ordered → customer is pending
        assert "919800000060" in phones