"""
pending_notifier.py
-------------------
Scheduled WhatsApp notifications built on top of
pending_orders.get_pending_customers():

  23:05 IST  →  notify_salespersons_pending()
  23:10 IST  →  send_management_summary()

Both are called from main.py scheduler jobs.

The 22:00 auto customer reminder was removed 2026-07-14 — it messaged every
pending customer. Customer reminders are now manual, from the owner-curated
📣 Broadcast screen (services/broadcast_service.py).
"""

from datetime import date

from sqlalchemy.orm import Session

from orderr_core.models.salesperson import Salesperson
from orderr_core.services.pending_orders import (
    get_pending_customers,
    get_delivery_date_for_now,
    active_daily_customers_q,
)
from orderr_core.services.notifier import send_whatsapp_template

from orderr_core.config import MANAGER_PHONE, PLANT_NAME

# ── Approved template names ───────────────────────────────────────────────────
TEMPLATE_SALESPERSON_PENDING = "salesperson_pending_orders"
TEMPLATE_MANAGER_SUMMARY     = "manager_daily_summary"

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

    params, total_received, total_active = build_management_summary_params(db, delivery_date)

    result = send_whatsapp_template(MANAGER_PHONE, TEMPLATE_MANAGER_SUMMARY, params)

    if result:
        print(f"\n✅ Management summary sent → {MANAGER_PHONE} ({total_received}/{total_active} received)\n")


def build_management_summary_params(db: Session, delivery_date: date) -> tuple[list, int, int]:
    """Compute the manager_daily_summary template params for `delivery_date`.

    Returns (params, total_received, total_active). Shared by the scheduled
    send_management_summary() and the on-demand ad-hoc manager summary so the
    two never drift apart.

    params = [PLANT_NAME, date_str, total_active, orders_received,
              pending_count, area_breakdown]  (all strings, Meta-template order)
    """
    grouped = get_pending_customers(db, delivery_date)

    total_active   = active_daily_customers_q(db).count()
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
        parts = [
            f"{area} ({len(names)} pending): {', '.join(names)}"
            for area, names in sorted(area_customers.items())
        ]
        area_breakdown = " | ".join(parts)
    else:
        area_breakdown = "None — all orders received"

    unassigned_pending = len(grouped.get(None, []))
    if unassigned_pending > 0:
        area_breakdown += f" | Unassigned: {unassigned_pending} pending"

    date_str = delivery_date.strftime("%d %B %Y")
    params = [
        PLANT_NAME,
        date_str,
        str(total_active),
        str(total_received),
        str(total_pending),
        area_breakdown,
    ]
    return params, total_received, total_active
