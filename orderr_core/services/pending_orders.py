"""
pending_orders.py
-----------------
Core logic for detecting customers who have not placed an order
for today's delivery.

Used by:
  - Scheduler jobs (customer reminders at 22:00, salesperson
    notifications at 23:05, management summary at 23:10)
  - Admin API  GET /admin/pending  (manual check anytime)
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from orderr_core.models.customer import Customer
from orderr_core.models.order import Order

# IST = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))
RESET_HOUR = 20  # 8 PM IST

def get_delivery_date_for_now() -> date:
    now_ist = datetime.now(IST)
    if now_ist.hour >= RESET_HOUR:
        return (now_ist + timedelta(days=1)).date()
    return now_ist.date()


def get_pending_customers(db: Session, delivery_date: date) -> dict:
    """
    Returns active daily-order customers who have NOT placed an order
    for `delivery_date`, grouped by salesperson_id.

    Customers without a salesperson assigned are excluded from grouping
    (they won't trigger notifications) but are returned under key None
    so the admin dashboard can surface them.

    Returns:
        {
            salesperson_id (int | None): [Customer, ...]
        }
    """

    # All active daily-order customers
    active_customers = (
        db.query(Customer)
        .filter(
            Customer.is_active == True,
            Customer.is_daily_order_customer == True,
            Customer.onboarding_status == "active"   # only fully onboarded
        )
        .all()
    )

    # Phones that have at least one order for this delivery_date
    # Order.delivery_date is stored as a string "YYYY-MM-DD" in the existing model
    delivery_date_str = delivery_date.strftime("%Y-%m-%d")

    ordered_phones = (
        db.query(Order.customer_phone)
        .filter(
            Order.business_date == delivery_date_str,
            Order.is_cancelled == False,
        )
        .all()
    )
    ordered_set = {row[0] for row in ordered_phones}

    # Filter to pending only
    pending = [c for c in active_customers if c.phone_number not in ordered_set]

    # Group by salesperson_id (None = unassigned)
    grouped: dict = defaultdict(list)
    for customer in pending:
        grouped[customer.salesperson_id].append(customer)

    return dict(grouped)


def get_pending_for_salesperson(
    db: Session,
    salesperson_id: int,
    delivery_date: date | None = None
) -> list:
    """
    Convenience wrapper — returns pending customers for one salesperson.
    """
    if delivery_date is None:
        delivery_date = get_delivery_date_for_now()

    grouped = get_pending_customers(db, delivery_date)
    return grouped.get(salesperson_id, [])
