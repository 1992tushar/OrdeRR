import os
import json
from datetime import datetime, date, timezone, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.order import Order
from app.services.notifier import send_whatsapp_template

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME    = os.getenv("PLANT_NAME", "Fluffy")
IST           = timezone(timedelta(hours=5, minutes=30))

# ── Approved template name ────────────────────────────────────────────────────
TEMPLATE_DAILY_REPORT = "manager_daily_report"


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


def generate_daily_report(db: Session) -> dict | None:
    """
    Generate consolidated daily order report.
    Returns dict with template parameters or None if no orders.
    """
    today = date.today()

    orders = db.query(Order).filter(
        func.date(Order.created_at) == today,
        Order.status.notin_(["pending_replace", "pending_repeat"]),
    ).order_by(Order.created_at.asc()).all()

    if not orders:
        return None

    clear_orders   = [o for o in orders if not o.is_unclear]
    unclear_orders = [o for o in orders if o.is_unclear]

    # Aggregate product totals
    product_totals: dict = {}
    for order in clear_orders:
        items = json.loads(order.parsed_items) if order.parsed_items else []
        for item in items:
            product  = normalize_product(item.get("product", "Unknown").strip())
            quantity = item.get("quantity", 0)
            unit     = item.get("unit", "kg").lower()
            key      = f"{product.lower()}||{unit}"
            if key not in product_totals:
                product_totals[key] = {"product": product, "unit": unit, "total_quantity": 0}
            product_totals[key]["total_quantity"] += quantity

    # Product summary string
    product_summary = ""
    for data in product_totals.values():
        qty     = data["total_quantity"]
        qty_str = str(int(qty)) if qty == int(qty) else str(qty)
        product_summary += f"{data['product']} - {qty_str} {data['unit']}\n"

    if unclear_orders:
        product_summary += f"\nUnclear orders: {len(unclear_orders)} (need follow up)"

    # Total items count
    total_items = sum(
        len(json.loads(o.parsed_items)) if o.parsed_items else 0
        for o in clear_orders
    )

    return {
        "date_str":        today.strftime("%d %B %Y"),
        "total_orders":    str(len(clear_orders)),
        "total_items":     str(total_items),
        "product_summary": product_summary.strip(),
    }


def send_daily_report(db: Session):
    """Send daily consolidated report to manager via approved template."""
    print("\n⏰ Generating Daily Report...")

    data = generate_daily_report(db)
    if not data:
        print("ℹ️ No orders found for today — report not sent")
        return

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