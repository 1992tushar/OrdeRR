import os
import json
from datetime import datetime, date, timezone

from sqlalchemy.orm import Session

from app.models.order import Order
from app.services.notifier import send_whatsapp_message

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")


def generate_daily_report(db: Session, report_type: str = "morning") -> str:
    """
    Generate consolidated order report.
    report_type: 'morning' (5am) or 'evening' (6pm)
    """

    today = date.today()

    # Use timezone-aware datetimes to match DateTime(timezone=True) column
    start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
    end = datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc)

    orders = db.query(Order).filter(
        Order.created_at >= start,
        Order.created_at <= end
    ).order_by(Order.created_at.asc()).all()

    if not orders:
        return None

    # Use .is_(True) — avoids SQLAlchemy anti-pattern warning
    clear_orders = [o for o in orders if not o.is_unclear]
    unclear_orders = [o for o in orders if o.is_unclear]

    # Aggregate all items across all orders
    product_totals = {}
    for order in clear_orders:
        items = json.loads(order.parsed_items) if order.parsed_items else []
        for item in items:
            product = item.get("product", "Unknown")
            quantity = item.get("quantity", 0)
            unit = item.get("unit", "kg")

            normalized = product.strip()
            skip_prefix = ["chicken", "whole", "tandoor", "spring", "half"]
            if not any(normalized.lower().startswith(w) for w in skip_prefix):
                normalized = f"Chicken {normalized}"

            key = f"{normalized}||{unit}"
            if key not in product_totals:
                product_totals[key] = {
                    "product": normalized,
                    "unit": unit,
                    "total_quantity": 0,
                    "orders_count": 0
                }
            product_totals[key]["total_quantity"] += quantity
            product_totals[key]["orders_count"] += 1

    emoji = "🌅" if report_type == "morning" else "🌆"
    report_time = "Morning" if report_type == "morning" else "Evening"

    report = (
        f"{emoji} *{PLANT_NAME} — {report_time} Order Report*\n"
        f"📅 Date: {today.strftime('%d %B %Y')}\n"
        f"⏰ Generated: {datetime.now().strftime('%I:%M %p')}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *TOTAL ORDERS: {len(clear_orders)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if product_totals:
        report += "*📊 CONSOLIDATED SUMMARY:*\n"
        for key, data in product_totals.items():
            report += f"• {data['product']} — *{data['total_quantity']} {data['unit']}*\n"

    report += "\n━━━━━━━━━━━━━━━━━━━━\n"
    report += "\n*📋 ORDER DETAILS:*\n"

    for i, order in enumerate(clear_orders, 1):
        items = json.loads(order.parsed_items) if order.parsed_items else []

        if order.delivery_date and order.delivery_time:
            delivery = f"{order.delivery_date} at {order.delivery_time}"
        elif order.delivery_date:
            delivery = order.delivery_date
        else:
            delivery = "Not specified"

        report += f"\n*{i}. Customer: {order.customer_phone}*\n"
        report += f"   🕐 Delivery: {delivery}\n"
        for item in items:
            report += f"   • {item['product']} — {item['quantity']} {item['unit']}\n"

    if unclear_orders:
        report += f"\n━━━━━━━━━━━━━━━━━━━━\n"
        report += f"⚠️ *UNCLEAR ORDERS: {len(unclear_orders)}*\n"
        report += "These need manual follow up:\n"
        for order in unclear_orders:
            report += f"• {order.customer_phone} — {order.raw_message[:50]}...\n"

    report += f"\n━━━━━━━━━━━━━━━━━━━━"
    # Use PLANT_NAME env var — was previously hardcoded as "Fluffy"
    report += f"\n_OrdeRR — {PLANT_NAME} Automation_"

    return report


def send_morning_report(db: Session):
    """Send 5am IST consolidated report to manager"""
    print("\n⏰ Generating 5AM Morning Report...")
    report = generate_daily_report(db, report_type="morning")
    if report:
        send_whatsapp_message(MANAGER_PHONE, report)
        print("✅ Morning report sent!")
    else:
        print("ℹ️ No orders found for today — report not sent")


def send_evening_report(db: Session):
    """Send 6pm IST consolidated report to manager"""
    print("\n⏰ Generating 6PM Evening Report...")
    report = generate_daily_report(db, report_type="evening")
    if report:
        send_whatsapp_message(MANAGER_PHONE, report)
        print("✅ Evening report sent!")
    else:
        print("ℹ️ No orders found for today — report not sent")