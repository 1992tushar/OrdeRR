import os
import json
from datetime import datetime, date, timezone, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.order import Order
from app.models.inbound_message import InboundMessage
from app.models.customer import Customer
from app.services.notifier import send_whatsapp_template, send_whatsapp_message
from app.services.order_service import get_current_business_date


MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME    = os.getenv("PLANT_NAME", "Fluffy")
IST           = timezone(timedelta(hours=5, minutes=30))

# ── Approved template name ────────────────────────────────────────────────────
TEMPLATE_DAILY_REPORT = "manager_daily_report"

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
def normalize_product(product: str) -> str:
    if "chicken" not in product.lower():
        return f"Chicken {product}"
    return product


def merge_items(items: list) -> list:
    merged = {}
    for item in items:
        product  = normalize_product(item.get("product", "Unknown").strip())
        quantity = item.get("quantity", 0)
        unit     = item.get("unit", "kg").lower()
        key      = f"{product.lower()}||{unit}"
        if key not in merged:
            merged[key] = {"product": product, "quantity": 0, "unit": unit}
        merged[key]["quantity"] += quantity
    return list(merged.values())


def get_todays_customer_notes(db: Session) -> list[dict]:
    """
    Fetch all inbound messages marked as NOTE for today.
    Returns list of dicts: {restaurant_name, phone, note, time}
    """
    today = datetime.now(IST).date()

    note_messages = (
        db.query(InboundMessage)
        .filter(
            func.date(InboundMessage.received_at) == today,
            InboundMessage.processing_status == "NOTE",
            InboundMessage.is_duplicate == False,
        )
        .order_by(InboundMessage.customer_phone, InboundMessage.received_at.asc())
        .all()
    )

    if not note_messages:
        return []

    # Look up restaurant names for each phone
    phones = list({m.customer_phone for m in note_messages})
    customers = db.query(Customer).filter(Customer.phone_number.in_(phones)).all()
    phone_to_name = {c.phone_number: c.restaurant_name or c.phone_number for c in customers}

    notes = []
    for m in note_messages:
        notes.append({
            "restaurant_name": phone_to_name.get(m.customer_phone, m.customer_phone),
            "phone":           m.customer_phone,
            "note":            m.raw_message or "",
            "time":            m.received_at.strftime("%I:%M %p") if m.received_at else "",
        })
    return notes


def generate_daily_report(db: Session) -> dict:
    """
    Generate consolidated daily order report.
    Always returns a dict — sends 'no orders' message when count is zero.
    """
    today = get_current_business_date()
    today_str = today.strftime("%Y-%m-%d")
    
    orders = db.query(Order).filter(
    Order.business_date == today_str,
    Order.is_cancelled == False,
    ).all()

    clear_orders   = [o for o in orders if not o.is_unclear]
    unclear_orders = [o for o in orders if o.is_unclear]

    # Aggregate product totals
    product_totals: dict = {}
    for order in clear_orders:
        items = _safe_list(order.parsed_items)

        for item in items:
            product  = normalize_product(item.get("product", "Unknown").strip())
            quantity = item.get("quantity", 0)
            unit     = item.get("unit", "kg").lower()
            key      = f"{product.lower()}||{unit}"
            if key not in product_totals:
                product_totals[key] = {"product": product, "unit": unit, "total_quantity": 0}
            product_totals[key]["total_quantity"] += quantity

    # Product summary string — pipe-separated, no newlines (Meta template requirement)
    if not orders:
        product_summary = "No orders received today"
    else:
        lines = []
        for data in product_totals.values():
            qty     = data["total_quantity"]
            qty_str = str(int(qty)) if qty == int(qty) else str(qty)
            lines.append(f"{data['product']} - {qty_str} {data['unit']}")

        if unclear_orders:
            lines.append(f"Unclear: {len(unclear_orders)} (need follow up)")

        product_summary = " | ".join(lines)

    # Total items count
    total_items = sum(len(_safe_list(o.parsed_items)) for o in clear_orders)


    return {
        "date_str":        today.strftime("%d %B %Y"),
        "total_orders":    str(len(clear_orders)),
        "total_items":     str(total_items),
        "product_summary": product_summary,
    }


def send_daily_report(db: Session):
    """Send daily consolidated report to manager via approved template.
    If any customer notes were received today, send a follow-up free-form message."""
    print("\n⏰ Generating Daily Report...")

    data = generate_daily_report(db)

    result = send_whatsapp_template(
        MANAGER_PHONE,
        TEMPLATE_DAILY_REPORT,
        [
            PLANT_NAME,
            data["date_str"],
            data["total_orders"],
            data["total_items"],
            data["product_summary"],
        ],
    )

    if result:
        print("✅ Daily report sent!")
    else:
        print("❌ Daily report failed!")

    # ── Customer notes follow-up ──────────────────────────────────────────────
    # If any customer sent a non-order message today, append as a separate
    # free-form message right after the report template.
    try:
        notes = get_todays_customer_notes(db)
        if notes:
            lines = [f"📝 *Customer Notes — {PLANT_NAME}*", f"{data['date_str']}", ""]
            for n in notes:
                time_str = f" ({n['time']})" if n['time'] else ""
                lines.append(f"• *{n['restaurant_name']}*{time_str}: {n['note']}")
            notes_msg = "\n".join(lines)
            send_whatsapp_message(MANAGER_PHONE, notes_msg)
            print(f"✅ Customer notes sent ({len(notes)} note(s))")
        else:
            print("ℹ️ No customer notes today — skipping notes message")
    except Exception as e:
        print(f"⚠️ Customer notes follow-up failed: {e}")