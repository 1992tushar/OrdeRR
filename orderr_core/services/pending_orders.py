"""
pending_orders.py
-----------------
Core logic for detecting customers who have not placed an order
for today's delivery.

The roster is the owner-curated 📣 Broadcast list (owner decision
2026-07-14) — NOT the legacy is_daily_order_customer flag, which no prod
customer ever had set. "Ordered" means an OrdeRR (WhatsApp) order OR a Vasy
invoice for the delivery date: most customers order by phone call, which
only surfaces in Vasy once the day's sales export is imported.

Used by:
  - Scheduler job (salesperson notifications at 23:15)
  - The public live status page (/r/<REPORT_LINK_KEY>)
  - Ad-hoc manager/salesperson WhatsApp replies
  - Admin API  GET /admin/pending  (manual check anytime)
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from orderr_core.models.customer import Customer
from orderr_core.models.order import Order
from orderr_core.models.broadcast_recipient import BroadcastRecipient
from orderr_core.models.vasy_invoice import VasyInvoice

# Canonical business-date helper lives in orderr_core.dates; re-exported here
# so `from pending_orders import get_delivery_date_for_now` keeps working.
from orderr_core.dates import get_delivery_date_for_now


def active_daily_customers_q(db: Session):
    """Query for the customers that Pending / status / reminders operate on:
    active customers on the 📣 Broadcast list. Callers apply .all() /
    .count() as needed."""
    return (db.query(Customer)
            .join(BroadcastRecipient, BroadcastRecipient.customer_id == Customer.id)
            .filter(Customer.is_active == True))


def ordered_sets(db: Session, delivery_date: date) -> tuple[set, set]:
    """(phones with an OrdeRR order, customer_ids with a Vasy invoice) for
    `delivery_date`. A customer counts as "ordered" when they appear in
    either — WhatsApp orders are live, phone orders arrive via the Vasy
    sales-export import."""
    delivery_date_str = delivery_date.strftime("%Y-%m-%d")
    ordered_phones = {row[0] for row in (
        db.query(Order.customer_phone)
        .filter(Order.business_date == delivery_date_str,
                Order.is_cancelled == False)
        .all())}
    invoiced_ids = {row[0] for row in (
        db.query(VasyInvoice.customer_id)
        .filter(VasyInvoice.invoice_date == delivery_date,
                VasyInvoice.customer_id != None)          # noqa: E711
        .all())}
    return ordered_phones, invoiced_ids


def get_pending_customers(db: Session, delivery_date: date) -> dict:
    """
    Returns broadcast-list customers who have NOT ordered (no OrdeRR order,
    no Vasy invoice) for `delivery_date`, grouped by salesperson_id.

    Customers without a salesperson assigned are excluded from grouping
    (they won't trigger notifications) but are returned under key None
    so the admin dashboard can surface them.

    Returns:
        {
            salesperson_id (int | None): [Customer, ...]
        }
    """
    active_customers = active_daily_customers_q(db).all()
    ordered_phones, invoiced_ids = ordered_sets(db, delivery_date)

    # Filter to pending only
    pending = [c for c in active_customers
               if c.phone_number not in ordered_phones and c.id not in invoiced_ids]

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
