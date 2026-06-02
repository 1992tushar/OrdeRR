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
from datetime import date

from sqlalchemy.orm import Session

from app.models.salesperson import Salesperson
from app.models.customer import Customer
from app.services.pending_orders import get_pending_customers, get_delivery_date_for_now
from app.services.notifier import send_whatsapp_message, send_whatsapp_template

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME    = os.getenv("PLANT_NAME", "Fluffy")

# ── Approved template names ───────────────────────────────────────────────────
TEMPLATE_SALESPERSON_PENDING = "salesperson_pending_orders"
TEMPLATE_MANAGER_SUMMARY     = "manager_daily_summary"


# ── 22:00 — Customer reminders ───────────────────────────────────────────────

def send_customer_reminders(db: Session, delivery_date: date | None = None):
    """
    Short nudge reminder to customers — always within 24hr window
    since customers message us to place orders daily.
    Sends a brief message only — customer types *order* to get the template.
    """
    if delivery_date is None:
        delivery_date = get_delivery_date_for_now()


    pending_customers = (
        db.query(Customer)
        .filter(
            Customer.is_active == True,
            Customer.is_daily_order_customer == True,
            Customer.onboarding_status == "active",
        )
        .all()
    )

    print(f"\n⏰ Customer Reminders — {len(pending_customers)} pending customers")

    sent = 0

    for customer in pending_customers:
        message = (
            f"⏰ *Reminder — {PLANT_NAME} Orders*\n\n"
            f"Hi {customer.restaurant_name},\n\n"
            f"You haven't placed your order yet today.\n\n"
            f"Type *order* to place your order now.\n\n"
            f"— {PLANT_NAME} Team"
        )
        result = send_whatsapp_message(customer.phone_number, message)
        if result:
            sent += 1
            print(f"   ✅ Reminder sent → {customer.restaurant_name} ({customer.phone_number})")

    print(f"   📤 Reminders sent: {sent}/{len(pending_customers)}\n")


# ── 23:05 — Salesperson notifications ────────────────────────────────────────

def notify_salespersons_pending(db: Session, delivery_date: date | None = None):
    """
    Sends each salesperson a WhatsApp list of their customers
    who have not yet ordered — uses approved template.
    Template: salesperson_pending_orders
    {{1}} = PLANT_NAME
    {{2}} = salesperson name
    {{3}} = customer list (single line, comma-separated)
    {{4}} = pending count
    """
    if delivery_date is None:
        delivery_date = get_delivery_date_for_now()

    grouped = get_pending_customers(db, delivery_date)

    assigned = {
        sp_id: customers
        for sp_id, customers in grouped.items()
        if sp_id is not None and len(customers) > 0
    }

    print(f"\n⏰ Salesperson Notifications — {len(assigned)} salespersons to notify")

    for salesperson_id, customers in assigned.items():

        salesperson = db.query(Salesperson).filter(
            Salesperson.id == salesperson_id,
            Salesperson.active == True,
        ).first()

        if not salesperson:
            print(f"   ⚠️  Salesperson id={salesperson_id} not found or inactive — skipped")
            continue

        # Single line — Meta rejects newlines/tabs in template parameters
        customer_list = ", ".join(
            f"{i + 1}. {c.restaurant_name}" + (f" ({c.area})" if c.area else "")
            for i, c in enumerate(customers)
        )

        result = send_whatsapp_template(
            salesperson.phone,
            TEMPLATE_SALESPERSON_PENDING,
            [PLANT_NAME, salesperson.name, customer_list, str(len(customers))],
        )

        if result:
            print(f"   ✅ Notified {salesperson.name} ({len(customers)} pending customers)")

    print()


# ── 23:10 — Management summary ───────────────────────────────────────────────

def send_management_summary(db: Session, delivery_date: date | None = None):
    """
    Sends the operations manager a daily completion summary
    via approved template.
    Template: manager_daily_summary
    {{1}} = PLANT_NAME
    {{2}} = date string
    {{3}} = total customers
    {{4}} = orders received
    {{5}} = pending count
    {{6}} = area breakdown (single line, pipe-separated — Meta rejects newlines)
    """
    if delivery_date is None:
        delivery_date = get_delivery_date_for_now()

    if not MANAGER_PHONE:
        print("⚠️  MANAGER_PHONE not set — management summary skipped")
        return

    grouped = get_pending_customers(db, delivery_date)

    total_active = (
        db.query(Customer)
        .filter(
            Customer.is_active == True,
            Customer.is_daily_order_customer == True,
            Customer.onboarding_status == "active",
        )
        .count()
    )

    all_pending    = [c for customers in grouped.values() for c in customers]
    total_pending  = len(all_pending)
    total_received = total_active - total_pending

    # Area-wise breakdown — single line, pipe-separated
    # e.g. "Talegaon (2 pending): Neha Hotel, Shubhada Hotel | Unassigned: 1 pending"
    area_customers: dict = {}
    for c in all_pending:
        area = c.area or "Unassigned"
        area_customers.setdefault(area, []).append(c.restaurant_name)

    if area_customers:
        parts = []
        for area, names in sorted(area_customers.items()):
            names_str = ", ".join(names)
            parts.append(f"{area} ({len(names)} pending): {names_str}")
        area_breakdown = " | ".join(parts)
    else:
        area_breakdown = "None — all orders received"

    unassigned_pending = len(grouped.get(None, []))
    if unassigned_pending > 0:
        area_breakdown += f" | Unassigned: {unassigned_pending} pending"

    date_str = delivery_date.strftime("%d %B %Y")

    result = send_whatsapp_template(
        MANAGER_PHONE,
        TEMPLATE_MANAGER_SUMMARY,
        [
            PLANT_NAME,
            date_str,
            str(total_active),
            str(total_received),
            str(total_pending),
            area_breakdown,
        ],
    )

    if result:
        print(f"\n✅ Management summary sent → {MANAGER_PHONE} ({total_received}/{total_active} received)\n")