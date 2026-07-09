"""
adhoc_reporter.py
-----------------
Handles on-demand report requests from manager and salespersons via WhatsApp.

Supports two input modes:
  1. Text keywords  — "report", "summary", "pending", etc. (legacy, still works)
  2. Button replies — interactive message button IDs from Quick Reply menus
       Manager buttons:     mgr_summary | mgr_daily_report | mgr_add_customer
       Salesperson buttons: sp_pending  | sp_help

Entry points:
  is_report_keyword(message)               → True if text keyword
  is_button_reply_id(button_id)            → True if known button ID
  handle_adhoc_report_request(phone, message, db)   → handles text keywords
  handle_button_reply(phone, button_id, db)          → handles button taps
"""

import os
from orderr_core.utils import fmt_qty
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
import json
from orderr_core.models.customer import Customer
from orderr_core.models.order import Order
from orderr_core.models.salesperson import Salesperson
from orderr_core.services.pending_orders import get_pending_customers, get_delivery_date_for_now
from orderr_core.services.template_parser import erp_display_name
from orderr_core.services.notifier import (
    send_whatsapp_template,
    send_whatsapp_message,
    send_manager_menu,
    send_salesperson_menu,
)
MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME    = os.getenv("PLANT_NAME", "Fluffy")
from orderr_core.constants import IST

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
    "aaj",
    "aaj ka report",
    "daily",
}

# ── Button reply IDs ──────────────────────────────────────────────────────────
MANAGER_BUTTON_IDS     = {"mgr_summary", "mgr_daily_report", "mgr_add_customer"}
SALESPERSON_BUTTON_IDS = {"sp_pending", "sp_help"}
ALL_BUTTON_IDS         = MANAGER_BUTTON_IDS | SALESPERSON_BUTTON_IDS


def is_report_keyword(message: str) -> bool:
    """Returns True if the message is a report keyword."""
    return message.strip().lower() in REPORT_KEYWORDS


def is_button_reply_id(button_id: str) -> bool:
    """Returns True if the button_id is one of our known Quick Reply button IDs."""
    return button_id.strip().lower() in ALL_BUTTON_IDS


def _get_sender_role(phone: str, db: Session) -> tuple[str, object]:
    """
    Returns (role, object) where:
      role = "manager" | "salesperson" | "unknown"
      object = None | Salesperson instance
    """
    from orderr_core.services.customer_service import normalize_phone
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


# ── Button reply handler (new) ────────────────────────────────────────────────

def handle_button_reply(phone: str, button_id: str, db: Session) -> bool:
    """
    Handle a Quick Reply button tap from manager or salesperson.
    Returns True if handled, False if unknown sender or unknown button.

    button_id values:
      mgr_summary       → manager daily summary template
      mgr_daily_report  → manager daily report template
      mgr_add_customer  → send instructions for ADD CUSTOMER text command
      sp_pending        → salesperson pending list
      sp_help           → salesperson help message
    """
    role, sp = _get_sender_role(phone, db)

    if role == "unknown":
        return False

    bid = button_id.strip().lower()

    if bid == "mgr_summary":
        _send_manager_summary(phone, db)
        return True

    if bid == "mgr_daily_report":
        _send_manager_daily_report_only(phone, db)
        return True

    if bid == "mgr_add_customer":
        send_whatsapp_message(
            phone,
            f"➕ *Add a Customer — {PLANT_NAME}*\n\n"
            f"Reply in this format:\n\n"
            f"*ADD CUSTOMER <phone> <restaurant name>*\n\n"
            f"Example:\n"
            f"ADD CUSTOMER 919876543210 Hotel Delicious\n\n"
            f"The customer will be registered immediately and sent a welcome message."
        )
        return True

    if bid == "sp_pending":
        if role == "salesperson" and sp:
            _send_salesperson_adhoc_report(phone, sp, db)
        return True

    if bid == "sp_help":
        name = sp.name if sp else "there"
        send_whatsapp_message(
            phone,
            f"❓ *Help — {PLANT_NAME}*\n\n"
            f"Hi {name}, here's what you can do:\n\n"
            f"📋 *My Pending* — See which of your customers haven't ordered yet today\n\n"
            f"You'll also receive an automatic notification at *11:05 PM* each night "
            f"listing any customers who still haven't placed their order.\n\n"
            f"Reply *menu* anytime to see this menu again."
        )
        return True

    return False


# ── Text keyword handler (existing, unchanged) ────────────────────────────────

def handle_adhoc_report_request(phone: str, message: str, db: Session) -> bool:
    """
    Main entry point for text keywords.
    Returns True if handled (sender is manager/salesperson),
    False if unknown phone (caller should process as regular message).
    """
    role, sp = _get_sender_role(phone, db)

    if role == "manager":
        _send_manager_adhoc_report(phone, db)
        return True

    if role == "salesperson":
        _send_salesperson_adhoc_report(phone, sp, db)
        return True

    return False


# ── Unrecognized message handler (new) ───────────────────────────────────────

def handle_unrecognized_internal_message(phone: str, db: Session) -> bool:
    """
    Called when an internal phone (manager/salesperson) sends a message
    that isn't a keyword, button reply, or ADD CUSTOMER command.
    Sends the appropriate Quick Reply menu.
    Returns True if handled, False if unknown phone.
    """
    role, sp = _get_sender_role(phone, db)

    if role == "manager":
        send_manager_menu(phone)
        return True

    if role == "salesperson":
        name = sp.name if sp else "there"
        send_salesperson_menu(phone, name)
        return True

    return False


# ── Manager report helpers ────────────────────────────────────────────────────

def _send_manager_adhoc_report(manager_phone: str, db: Session):
    """Sends summary + daily report (if orders exist). Used by text keyword."""
    _send_manager_summary(manager_phone, db)
    _send_manager_daily_report_only(manager_phone, db)


def _send_manager_summary(manager_phone: str, db: Session):
    """Sends the manager daily summary template."""
    from orderr_core.services.order_service import get_current_business_date

    delivery_date = get_current_business_date()

    date_str      = delivery_date.strftime("%d %B %Y")

    print(f"\n📊 Manager summary requested by {manager_phone}")

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

# Add this helper after the TEMPLATE_DAILY_REPORT line:
def _safe_list(value) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, str):      # double-encoded
            parsed = json.loads(parsed)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _send_manager_daily_report_only(manager_phone: str, db: Session):
    """Sends the manager daily report template (product totals). Skips if no orders."""
    import json
    from orderr_core.services.order_service import get_current_business_date

    delivery_date = get_current_business_date()
    date_str      = delivery_date.strftime("%d %B %Y")
    today_str     = delivery_date.strftime("%Y-%m-%d")

    orders = (
        db.query(Order)
        .filter(
            Order.business_date == today_str,
            Order.is_cancelled == False,
            Order.is_unclear == False,
        )
        .all()
    )

    if not orders:
        print(f"   ℹ️  No orders today — skipping free-form message (window may be closed)")
        return

    product_totals: dict = {}
    for order in orders:
        items = _safe_list(order.parsed_items)
        for item in items:
            if not isinstance(item, dict):
                continue
            key = item.get("product", "Unknown")
            product_totals[key] = product_totals.get(key, 0) + item["quantity"]

    total_items_count = sum(product_totals.values())
    items_text        = ", ".join(f"{erp_display_name(p)} x{fmt_qty(q)}" for p, q in product_totals.items())
    product_summary   = " | ".join(f"{erp_display_name(p)}: {fmt_qty(q)}" for p, q in product_totals.items())

    send_whatsapp_template(
        manager_phone,
        TEMPLATE_MANAGER_REPORT,
        [PLANT_NAME, date_str, str(len(orders)), items_text, product_summary],
    )
    print(f"   ✅ Daily report sent → {len(orders)} orders, {total_items_count} items")

    # ── Email delivery sheet ──────────────────────────────────────────────────
    try:
        from orderr_core.services.reporter import generate_daily_report, _send_email_report
        report_data = generate_daily_report(db)
        _send_email_report(report_data, [])
        print("   ✅ Email report sent")
    except Exception as e:
        print(f"   ⚠️ Email report failed: {e}")


# ── Salesperson ad hoc report (unchanged logic) ───────────────────────────────

def _send_salesperson_adhoc_report(sp_phone: str, sp: Salesperson, db: Session):
    """Sends the salesperson their pending customer list."""
    delivery_date = get_delivery_date_for_now()

    print(f"\n📋 Salesperson report requested by {sp.name} ({sp_phone})")

    grouped    = get_pending_customers(db, delivery_date)
    sp_pending = grouped.get(sp.id, [])

    if not sp_pending:
        send_whatsapp_message(
            sp_phone,
            f"✅ *All Clear — {PLANT_NAME}*\n\n"
            f"Hi {sp.name},\n\n"
            f"All your customers have placed their orders for today! 🎉\n\n"
            f"— {PLANT_NAME} Team"
        )
        print(f"   ✅ All-clear sent → {sp.name}")
        return

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
