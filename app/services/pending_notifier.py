"""
pending_notifier.py
-------------------
Three scheduled WhatsApp notifications built on top of
pending_orders.get_pending_customers():

  22:00 IST  →  send_customer_reminders()
  23:05 IST  →  notify_salespersons_pending()
  23:10 IST  →  send_management_summary()

All three are called from main.py scheduler jobs.
"""

import os
from collections import Counter
from datetime import date

from sqlalchemy.orm import Session

from app.models.salesperson import Salesperson
from app.models.customer import Customer
from app.services.pending_orders import get_pending_customers, get_delivery_date_for_now
from app.services.notifier import send_whatsapp_message

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")


# ── 22:00 — Customer reminders ───────────────────────────────────────────────

def send_customer_reminders(db: Session, delivery_date: date | None = None):
    """
    Sends a WhatsApp reminder to every pending customer at 10 PM.
    Only active, daily-order, assigned customers are messaged.
    (Unassigned customers — grouped[None] — are silently skipped.)
    """

    if delivery_date is None:
        delivery_date = get_delivery_date_for_now()

    grouped = get_pending_customers(db, delivery_date)

    # Flatten all assigned customers only
    pending_customers = [
        c
        for sp_id, customers in grouped.items()
        if sp_id is not None
        for c in customers
    ]

    print(f"\n⏰ Customer Reminders — {len(pending_customers)} pending customers")

    sent = 0
    for customer in pending_customers:
        message = (
            f"🐔 *{PLANT_NAME} — Order Reminder*\n\n"
            f"Hi {customer.restaurant_name},\n\n"
            f"We haven't received your order for tomorrow yet.\n"
            f"Please place your order before 11:00 PM.\n\n"
            f"Reply with your order or type *order* to get the menu.\n\n"
            f"— {PLANT_NAME} Team"
        )
        result = send_whatsapp_message(customer.phone_number, message)
        if result:
            sent += 1
            print(f"   ✅ Reminder sent → {customer.restaurant_name} ({customer.phone_number})")

    print(f"   📤 Reminders sent: {sent}/{len(pending_customers)}\n")


# ── 23:05 — Salesperson notifications ───────────────────────────────────────

def notify_salespersons_pending(db: Session, delivery_date: date | None = None):
    """
    Sends each salesperson a WhatsApp list of their customers
    who have not yet ordered.
    """

    if delivery_date is None:
        delivery_date = get_delivery_date_for_now()

    grouped = get_pending_customers(db, delivery_date)

    # Remove unassigned bucket — salespersons only
    assigned = {
        sp_id: customers
        for sp_id, customers in grouped.items()
        if sp_id is not None and len(customers) > 0
    }

    print(f"\n⏰ Salesperson Notifications — {len(assigned)} salespersons to notify")

    for salesperson_id, customers in assigned.items():

        salesperson = db.query(Salesperson).filter(
            Salesperson.id == salesperson_id,
            Salesperson.active == True
        ).first()

        if not salesperson:
            print(f"   ⚠️  Salesperson id={salesperson_id} not found or inactive — skipped")
            continue

        # Build customer list lines
        lines = "\n".join(
            f"{i + 1}. {c.restaurant_name}" + (f" ({c.area})" if c.area else "")
            for i, c in enumerate(customers)
        )

        message = (
            f"📋 *Pending Orders — {PLANT_NAME}*\n\n"
            f"Hi {salesperson.name}, the following customers haven't ordered yet:\n\n"
            f"{lines}\n\n"
            f"Total Pending: *{len(customers)}*\n\n"
            f"Please follow up with them.\n"
            f"— {PLANT_NAME} Team"
        )

        result = send_whatsapp_message(salesperson.phone, message)
        if result:
            print(
                f"   ✅ Notified {salesperson.name} "
                f"({len(customers)} pending customers)"
            )

    print()


# ── 23:10 — Management summary ───────────────────────────────────────────────

def send_management_summary(db: Session, delivery_date: date | None = None):
    """
    Sends the operations manager a daily completion summary:
    total customers, orders received, pending count, area-wise breakdown.
    """

    if delivery_date is None:
        delivery_date = get_delivery_date_for_now()

    if not MANAGER_PHONE:
        print("⚠️  MANAGER_PHONE not set — management summary skipped")
        return

    grouped = get_pending_customers(db, delivery_date)

    # Total active daily-order customers (onboarded)
    total_active = (
        db.query(Customer)
        .filter(
            Customer.is_active == True,
            Customer.is_daily_order_customer == True,
            Customer.onboarding_status == "completed"
        )
        .count()
    )

    # Flatten all pending (assigned + unassigned)
    all_pending = [c for customers in grouped.values() for c in customers]
    total_pending = len(all_pending)
    total_received = total_active - total_pending

    # Area-wise pending breakdown
    area_counts = Counter(c.area or "Unassigned" for c in all_pending)
    area_lines = "\n".join(
        f"  {area:<15}: {count} pending"
        for area, count in sorted(area_counts.items())
    )

    # Unassigned customers note
    unassigned_pending = len(grouped.get(None, []))
    unassigned_note = (
        f"\n⚠️  Unassigned customers pending: {unassigned_pending}"
        if unassigned_pending > 0
        else ""
    )

    message = (
        f"📊 *Daily Order Status — {PLANT_NAME}*\n"
        f"📅 {delivery_date.strftime('%d %B %Y')}\n\n"
        f"Total Customers: *{total_active}*\n"
        f"Orders Received: *{total_received}*\n"
        f"Pending Orders:  *{total_pending}*\n"
        f"\n*Area-wise Pending:*\n"
        f"{area_lines if area_lines else '  None — all orders received ✅'}"
        f"{unassigned_note}\n\n"
        f"_OrdeRR — {PLANT_NAME} Automation_"
    )

    result = send_whatsapp_message(MANAGER_PHONE, message)
    if result:
        print(
            f"\n✅ Management summary sent → {MANAGER_PHONE} "
            f"({total_received}/{total_active} received)\n"
        )
