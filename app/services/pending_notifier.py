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
from app.services.notifier import send_whatsapp_template

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME    = os.getenv("PLANT_NAME", "Fluffy")

# ── Approved template names ───────────────────────────────────────────────────
TEMPLATE_CUSTOMER_REMINDER   = "customer_daily_reminder"
TEMPLATE_SALESPERSON_PENDING = "salesperson_pending_orders"
TEMPLATE_MANAGER_SUMMARY     = "manager_daily_summary"


# ── 22:00 — Customer reminders ───────────────────────────────────────────────

def send_customer_reminders(db: Session, delivery_date: date | None = None):
    """
    Short nudge reminder to customers using an approved Meta utility template.
    Bypasses the 24hr window restriction for inactive/silent customers.
    
    Template: customer_daily_reminder
    {{1}} = PLANT_NAME
    {{2}} = restaurant_name
    """
    if delivery_date is None:
        delivery_date = get_delivery_date_for_now()

    # FIX: Instead of raw querying ALL active customers, filter using the pending list engine
    grouped = get_pending_customers(db, delivery_date)
    all_pending_customers = [c for customers in grouped.values() for c in customers]

    print(f"\n⏰ Customer Reminders — {len(all_pending_customers)} pending customers identified")

    sent = 0

    for customer in all_pending_customers:
        # Using the approved template to send exactly what was used in the free-form structure safely
        result = send_whatsapp_template(
            customer.phone_number,
            TEMPLATE_CUSTOMER_REMINDER,
            [PLANT_NAME, customer.restaurant_name]
        )
        if result:
            sent += 1
            print(f"   ✅ Template reminder sent → {customer.restaurant_name} ({customer.phone_number})")

    print(f"   📤 Reminders sent: {sent}/{len(all_pending_customers)}\n")


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