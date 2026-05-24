import os
import json
from datetime import datetime, date, timezone, timedelta

from sqlalchemy.orm import Session

from app.models.order import Order
from app.services.notifier import send_whatsapp_message

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")

# IST = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))


def normalize_product(product: str) -> str:
    """Prefix 'Chicken' if not already in the product name."""
    if "chicken" not in product.lower():
        return f"Chicken {product}"
    return product


def merge_items(items: list) -> list:
    """
    Merge duplicate products within a single order.
    e.g. Wings 4kg + Wings 2kg → Wings 6kg
    """
    merged = {}
    for item in items:
        product = normalize_product(item.get("product", "Unknown").strip())
        quantity = item.get("quantity", 0)
        unit = item.get("unit", "kg").lower()
        key = f"{product.lower()}||{unit}"
        if key not in merged:
            merged[key] = {"product": product, "quantity": 0, "unit": unit}
        merged[key]["quantity"] += quantity
    return list(merged.values())


def generate_daily_report(db: Session) -> str:
    """Generate consolidated daily order report."""

    today = date.today()
    today_str = today.strftime("%Y-%m-%d")

    orders = db.query(Order).filter(
        Order.created_at.like(f"{today_str}%")
    ).order_by(Order.created_at.asc()).all()

    if not orders:
        return None

    clear_orders = [o for o in orders if not o.is_unclear]
    unclear_orders = [o for o in orders if o.is_unclear]

    # Aggregate totals across all clear orders
    product_totals = {}
    for order in clear_orders:
        items = json.loads(order.parsed_items) if order.parsed_items else []
        for item in items:
            product = normalize_product(item.get("product", "Unknown").strip())
            quantity = item.get("quantity", 0)
            unit = item.get("unit", "kg").lower()

            key = f"{product.lower()}||{unit}"
            if key not in product_totals:
                product_totals[key] = {
                    "product": product,
                    "unit": unit,
                    "total_quantity": 0,
                    "orders_count": 0
                }
            product_totals[key]["total_quantity"] += quantity
            product_totals[key]["orders_count"] += 1

    now_ist = datetime.now(IST)

    report = (
        f"📊 *{PLANT_NAME} — Daily Order Report*\n"
        f"📅 Date: {today.strftime('%d %B %Y')}\n"
        f"⏰ Generated: {now_ist.strftime('%I:%M %p')} IST\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *TOTAL ORDERS: {len(clear_orders)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if product_totals:
        report += "*📊 CONSOLIDATED SUMMARY:*\n"
        for key, data in product_totals.items():
            qty = data['total_quantity']
            qty_str = str(int(qty)) if qty == int(qty) else str(qty)
            report += f"• {data['product']} — *{qty_str} {data['unit']}*\n"

    report += "\n━━━━━━━━━━━━━━━━━━━━\n"
    report += "\n*📋 ORDER DETAILS:*\n"

    for i, order in enumerate(clear_orders, 1):
        raw_items = json.loads(order.parsed_items) if order.parsed_items else []

        # Merge duplicates + normalize names in detail section
        items = merge_items(raw_items)

        display_name = order.customer_name or order.customer_phone

        if order.delivery_date and order.delivery_time:
            delivery = f"{order.delivery_date} at {order.delivery_time}"
        elif order.delivery_date:
            delivery = order.delivery_date
        else:
            delivery = "Not specified"

        report += f"\n*{i}. {display_name}*\n"
        report += f"   📱 {order.customer_phone}\n"
        report += f"   🕐 Delivery: {delivery}\n"
        for item in items:
            qty = item['quantity']
            qty_str = str(int(qty)) if qty == int(qty) else str(qty)
            report += f"   • {item['product']} — {qty_str} {item['unit']}\n"

    if unclear_orders:
        report += f"\n━━━━━━━━━━━━━━━━━━━━\n"
        report += f"⚠️ *UNCLEAR ORDERS: {len(unclear_orders)}*\n"
        report += "These need manual follow up:\n"
        for order in unclear_orders:
            display_name = order.customer_name or order.customer_phone
            raw = order.raw_message or ""
            preview = raw[:50] + ("..." if len(raw) > 50 else "")
            report += f"• {display_name} — \"{preview}\"\n"

    report += f"\n━━━━━━━━━━━━━━━━━━━━"
    report += f"\n_OrdeRR — {PLANT_NAME} Automation_"

    return report


def send_daily_report(db: Session):
    """Send daily consolidated report to manager at configured REPORT_TIME"""
    print("\n⏰ Generating Daily Report...")
    report = generate_daily_report(db)
    if report:
        send_whatsapp_message(MANAGER_PHONE, report)
        print("✅ Daily report sent!")
    else:
        print("ℹ️ No orders found for today — report not sent")