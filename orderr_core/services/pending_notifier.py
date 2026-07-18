"""
pending_notifier.py
-------------------
The one remaining scheduled WhatsApp notification built on top of
pending_orders.get_pending_customers():

  23:15 IST  →  notify_salespersons_pending()   (main.py scheduler job)

Removed 2026-07-14 (owner decision — reports live on the fixed status page
/r/{REPORT_LINK_KEY} instead, long lists are unreadable in WhatsApp):
  - 22:00 auto customer reminder → manual 📣 Broadcast screen
  - 23:10 management summary (manager_daily_summary template)
"""

from datetime import date

from sqlalchemy.orm import Session

from orderr_core.models.salesperson import Salesperson
from orderr_core.services.pending_orders import (
    get_pending_customers,
    get_delivery_date_for_now,
)
from orderr_core.services.notifier import send_whatsapp_template

from orderr_core.config import PLANT_NAME

# ── Approved template names ───────────────────────────────────────────────────
TEMPLATE_SALESPERSON_PENDING = "salesperson_pending_orders"

# ── 23:15 — Salesperson notifications ────────────────────────────────────────

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
