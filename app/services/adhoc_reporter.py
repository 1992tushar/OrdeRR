"""
adhoc_reporter.py
-----------------
Handles on-demand report requests from manager and salespersons via WhatsApp.

When manager or salesperson texts one of the REPORT_KEYWORDS, this module:
  1. Detects the sender's role (manager / salesperson / unknown)
  2. Sends the appropriate report back immediately

Keywords: "report", "send report", "summary", "send summary", "pending"

Manager gets:
  - manager_daily_summary template (orders received vs pending)
  - manager_daily_report template if orders exist for today

Salesperson gets:
  - salesperson_pending_orders template if they have pending customers
  - Free-form "all clear" message if all their customers have ordered

Unknown phones: returns False — caller should treat as a regular message.

Usage in order_service.py (insert near top of process_incoming_order()):
    from app.services.adhoc_reporter import handle_adhoc_report_request, is_report_keyword
    if is_report_keyword(message):
        handled = handle_adhoc_report_request(customer_phone, message, db)
        if handled:
            return "adhoc_report_sent"
"""

import os
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.customer import Customer
from app.models.order import Order
from app.models.salesperson import Salesperson
from app.services.pending_orders import get_pending_customers, get_delivery_date_for_now
from app.services.notifier import send_whatsapp_template, send_whatsapp_message

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME    = os.getenv("PLANT_NAME", "Fluffy")
IST           = timezone(timedelta(hours=5, minutes=30))

# ── Approved template names ───────────────────────────────────────────────────
TEMPLATE_MANAGER_SUMMARY  = "manager_daily_summary"
TEMPLATE_MANAGER_REPORT   = "manager_daily_report"
TEMPLATE_SP_PENDING       = "salesperson_pending_orders"

# ── Keywords that trigger an ad hoc report ────────────────────────────────────
REPORT_KEYWORDS = {
    "report",
    "send report",
    "summary",
    "send summary",
    "pending",
    "status",
    "send status",
    "today",
    "today report",
    "aaj",           # Hindi: today
    "aaj ka report",
}


def is_report_keyword(message: str) -> bool:
    """Returns True if the message is a report keyword."""
    return message.strip().lower() in REPORT_KEYWORDS


def _get_sender_role(phone: str, db: Session) -> tuple[str, object]:
    """
    Returns (role, object) where:
      role = "manager" | "salesperson" | "unknown"
      object = None | Salesperson instance
    """
    from app.services.customer_service import normalize_phone
    normalized = normalize_phone(phone)

    if MANAGER_PHONE and normalize_phone(MANAGER_PHONE) == normalized:
        return "manager", None

    sp = db.query(Salesperson).filter(
        Salesperson.phone == normalized,
        Salesperson.active == True,
    ).first()
    if sp:
        return "salesperson", sp

    return "unknown", None


def handle_adhoc_report_request(phone: str, message: str, db: Session) -> bool:
    """
    Main entry point. Returns True if handled (sender is manager/salesperson),
    False if unknown phone (caller should process as regular message).
    """
    role, sp = _get_sender_role(phone, db)

    if role == "manager":
        _send_manager_adhoc_report(phone, db)
        return True

    if role == "salesperson":
        _send_salesperson_adhoc_report(phone, sp, db)
        return True

    return False  # unknown sender — treat as regular order message


# ── Manager ad hoc report ─────────────────────────────────────────────────────

def _send_manager_adhoc_report(manager_phone: str, db: Session):
    """
    Sends the manager:
    1. Daily summary (pending/received breakdown) — always
    2. Daily report (product totals) — only if orders exist today
    """
    delivery_date = get_delivery_date_for_now()
    date_str      = delivery_date.strftime("%d %B %Y")
    today_str     = delivery_date.strftime("%Y-%m-%d")

    print(f"\n📊 Ad hoc manager report requested by {manager_phone}")

    # ── Summary ──────────────────────────────────────────────────────────────
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

    all_pending   = [c for customers in grouped.values() for c in customers]
    total_pending = len(all_pending)
    total_received = total_active - total_pending

    # Area breakdown — pipe-separated (Meta rejects newlines)
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

    send_whatsapp_template(
        manager_phone,
        TEMPLATE_MANAGER_SUMMARY,
        [PLANT_NAME, date_str, str(total_active), str(total_received), str(total_pending), area_breakdown],
    )
    print(f"   ✅ Summary sent → {total_received}/{total_active} received")

    # ── Daily report — only if orders exist ──────────────────────────────────
    orders = (
        db.query(Order)
        .filter(
            Order.delivery_date == today_str,
            Order.is_cancelled == False,
            Order.is_unclear == False,
        )
        .all()
    )

    if not orders:
        print(f"   ℹ️  No orders today — skipping daily report")
        return

    # Build product summary
    import json
    product_totals: dict = {}
    for order in orders:
        try:
            items = json.loads(order.parsed_items) if order.parsed_items else []
        except Exception:
            items = []
        for item in items:
            key = item["product"]
            product_totals[key] = product_totals.get(key, 0) + item["quantity"]

    total_items_count = sum(product_totals.values())
    items_text  = ", ".join(f"{p} x{int(q) if q == int(q) else q}" for p, q in product_totals.items())
    product_summary = " | ".join(f"{p}: {int(q) if q == int(q) else q}" for p, q in product_totals.items())

    send_whatsapp_template(
        manager_phone,
        TEMPLATE_MANAGER_REPORT,
        [PLANT_NAME, date_str, str(len(orders)), items_text, product_summary],
    )
    print(f"   ✅ Daily report sent → {len(orders)} orders, {total_items_count} items")


# ── Salesperson ad hoc report ─────────────────────────────────────────────────

def _send_salesperson_adhoc_report(sp_phone: str, sp: Salesperson, db: Session):
    """
    Sends the salesperson their pending customer list.
    If all customers have ordered, sends a free-form "all clear" message.
    """
    delivery_date = get_delivery_date_for_now()

    print(f"\n📋 Ad hoc salesperson report requested by {sp.name} ({sp_phone})")

    grouped = get_pending_customers(db, delivery_date)
    sp_pending = grouped.get(sp.id, [])

    if not sp_pending:
        # All clear — free-form message (customer already messaged, window open)
        send_whatsapp_message(
            sp_phone,
            f"✅ *All Clear — {PLANT_NAME}*\n\n"
            f"Hi {sp.name},\n\n"
            f"All your customers have placed their orders for today! 🎉\n\n"
            f"— {PLANT_NAME} Team"
        )
        print(f"   ✅ All-clear sent → {sp.name} (no pending customers)")
        return

    # Build pending list — single line, Meta rejects newlines in template params
    customer_list = ", ".join(
        f"{i+1}. {c.restaurant_name}" + (f" ({c.area})" if c.area else "")
        for i, c in enumerate(sp_pending)
    )

    send_whatsapp_template(
        sp_phone,
        TEMPLATE_SP_PENDING,
        [PLANT_NAME, sp.name, customer_list, str(len(sp_pending))],
    )
    print(f"   ✅ Pending list sent → {sp.name} ({len(sp_pending)} pending)")
