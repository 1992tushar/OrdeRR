"""
test_order_flow.py
------------------
Integration tests for order placement, cancellation,
repeat, and replace flows in order_service.py
"""

import pytest
import json
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
TODAY = datetime.now(IST).strftime("%Y-%m-%d")
PHONE = "919800000030"


@pytest.fixture(autouse=True)
def mock_all_sends():
    with patch("app.services.notifier.send_whatsapp_message") as mock:
        mock.return_value = {"status": "mocked"}
        yield mock


# ── Order Placement ───────────────────────────────────────────────────────────

class TestOrderPlacement:

    def test_simple_order_creates_record(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        from app.models.order import Order
        result = process_incoming_order(db, registered_customer.phone_number, "Breast Boneless - 5 kg")
        assert result["order_id"] is not None
        order = db.query(Order).filter(Order.id == result["order_id"]).first()
        assert order is not None

    def test_order_delivery_date_is_today(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        from app.models.order import Order
        result = process_incoming_order(db, registered_customer.phone_number, "Wings - 2 kg")
        order = db.query(Order).filter(Order.id == result["order_id"]).first()
        assert order.delivery_date == TODAY

    def test_order_items_saved_correctly(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        from app.models.order import Order
        result = process_incoming_order(db, registered_customer.phone_number, "Curry Cut - 3 kg")
        order = db.query(Order).filter(Order.id == result["order_id"]).first()
        items = json.loads(order.parsed_items)
        assert items[0]["product"] == "Curry Cut"
        assert items[0]["quantity"] == 3.0

    def test_shortcode_order_works(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        result = process_incoming_order(db, registered_customer.phone_number, "CC 5\nBBL 2")
        assert result["order_id"] is not None
        assert result["status"] not in ("unclear_message", "awaiting_restaurant_name")

    def test_unclear_message_no_order(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        from app.models.order import Order
        result = process_incoming_order(db, registered_customer.phone_number, "kal bhejdo yaar")
        assert result["status"] == "unclear_message"
        orders = db.query(Order).filter(
            Order.customer_phone == registered_customer.phone_number,
            Order.is_cancelled == False,
        ).all()
        assert len(orders) == 0

    def test_menu_keyword_no_order(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        result = process_incoming_order(db, registered_customer.phone_number, "order")
        assert result["status"] == "menu_sent"
        assert result["order_id"] is None

    def test_multiple_items_order(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        from app.models.order import Order
        msg = "WS Tandoor - 10 nos\nBreast Boneless - 3 kg\nCurry Cut - 5 kg"
        result = process_incoming_order(db, registered_customer.phone_number, msg)
        order = db.query(Order).filter(Order.id == result["order_id"]).first()
        items = json.loads(order.parsed_items)
        assert len(items) == 3

    def test_order_status_received(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        from app.models.order import Order
        result = process_incoming_order(db, registered_customer.phone_number, "DS 2 kg")
        order = db.query(Order).filter(Order.id == result["order_id"]).first()
        assert order.status == "received"

    def test_order_not_cancelled(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        from app.models.order import Order
        result = process_incoming_order(db, registered_customer.phone_number, "LB 3 kg")
        order = db.query(Order).filter(Order.id == result["order_id"]).first()
        assert order.is_cancelled is False


# ── Order Cancellation ────────────────────────────────────────────────────────

class TestOrderCancellation:

    def test_cancel_existing_order(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        from app.models.order import Order
        # Place order first
        process_incoming_order(db, registered_customer.phone_number, "Wings - 2 kg")
        # Cancel it
        result = process_incoming_order(db, registered_customer.phone_number, "cancel")
        assert result["status"] == "order_cancelled"

    def test_cancel_marks_order_cancelled(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        from app.models.order import Order
        process_incoming_order(db, registered_customer.phone_number, "Wings - 2 kg")
        process_incoming_order(db, registered_customer.phone_number, "cancel")
        orders = db.query(Order).filter(
            Order.customer_phone == registered_customer.phone_number,
            Order.is_cancelled == True,
        ).all()
        assert len(orders) == 1

    def test_cancel_no_order_returns_correct_status(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        result = process_incoming_order(db, registered_customer.phone_number, "cancel")
        assert result["status"] == "no_order_to_cancel"

    def test_cancel_hindi_keyword(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        process_incoming_order(db, registered_customer.phone_number, "Wings - 2 kg")
        result = process_incoming_order(db, registered_customer.phone_number, "cancel karo")
        assert result["status"] == "order_cancelled"


# ── Repeat Order ──────────────────────────────────────────────────────────────

class TestRepeatOrder:

    def _seed_previous_order(self, db, customer):
        """
        Directly insert a completed order from yesterday so
        get_last_order() can find it (it filters is_cancelled==False).
        """
        import json
        from app.models.order import Order
        from datetime import datetime, timezone, timedelta
        IST = timezone(timedelta(hours=5, minutes=30))
        yesterday = (datetime.now(IST).date() - timedelta(days=1)).strftime("%Y-%m-%d")
        order = Order(
            customer_phone=customer.phone_number,
            customer_name=customer.restaurant_name,
            raw_message="Wings - 2 kg",
            parsed_items=json.dumps([{"product": "Wings", "quantity": 2.0, "unit": "kg"}]),
            delivery_date=yesterday,
            is_cancelled=False,
            is_unclear=False,
            status="received",
        )
        db.add(order)
        db.commit()
        return order

    def test_repeat_no_history(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        result = process_incoming_order(db, registered_customer.phone_number, "same")
        assert result["status"] == "no_last_order"

    def test_repeat_creates_pending_order(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        self._seed_previous_order(db, registered_customer)
        result = process_incoming_order(db, registered_customer.phone_number, "same")
        assert result["status"] == "repeat_requested"

    def test_repeat_confirm_yes_creates_order(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        self._seed_previous_order(db, registered_customer)
        process_incoming_order(db, registered_customer.phone_number, "same")
        result = process_incoming_order(db, registered_customer.phone_number, "yes")
        assert result["status"] == "repeat_confirmed"
        assert result["order_id"] is not None

    def test_repeat_confirm_no_cancels(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        self._seed_previous_order(db, registered_customer)
        process_incoming_order(db, registered_customer.phone_number, "same")
        result = process_incoming_order(db, registered_customer.phone_number, "no")
        assert result["status"] == "repeat_cancelled"

    def test_repeat_keyword_wahi_bhejo(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        self._seed_previous_order(db, registered_customer)
        result = process_incoming_order(db, registered_customer.phone_number, "wahi bhejo")
        assert result["status"] == "repeat_requested"


# ── Replace Order (second order same day) ─────────────────────────────────────

class TestReplaceOrder:

    def test_second_order_before_cutoff_triggers_replace(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        # Mock time to before 9 AM
        with patch("app.services.order_service.datetime") as mock_dt:
            mock_ist = datetime(2026, 5, 31, 7, 0, 0, tzinfo=IST)
            mock_dt.now.return_value = mock_ist
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            process_incoming_order(db, registered_customer.phone_number, "Wings - 2 kg")
            result = process_incoming_order(db, registered_customer.phone_number, "Breast Boneless - 3 kg")
        assert result["status"] == "replace_requested"

    def test_replace_confirm_yes(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        with patch("app.services.order_service.datetime") as mock_dt:
            mock_ist = datetime(2026, 5, 31, 7, 0, 0, tzinfo=IST)
            mock_dt.now.return_value = mock_ist
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            process_incoming_order(db, registered_customer.phone_number, "Wings - 2 kg")
            process_incoming_order(db, registered_customer.phone_number, "Breast Boneless - 3 kg")
            result = process_incoming_order(db, registered_customer.phone_number, "yes")
        assert result["status"] == "replace_confirmed"

    def test_replace_confirm_no_keeps_original(self, db, registered_customer):
        from app.services.order_service import process_incoming_order
        from app.models.order import Order
        with patch("app.services.order_service.datetime") as mock_dt:
            mock_ist = datetime(2026, 5, 31, 7, 0, 0, tzinfo=IST)
            mock_dt.now.return_value = mock_ist
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            process_incoming_order(db, registered_customer.phone_number, "Wings - 2 kg")
            process_incoming_order(db, registered_customer.phone_number, "Breast Boneless - 3 kg")
            result = process_incoming_order(db, registered_customer.phone_number, "no")
        assert result["status"] == "replace_cancelled"
        # Original order should still be active
        active = db.query(Order).filter(
            Order.customer_phone == registered_customer.phone_number,
            Order.is_cancelled == False,
            Order.status == "received",
        ).first()
        assert active is not None