import os
import json
from datetime import datetime, date
from sqlalchemy.orm import Session
from app.models.order import Order
from app.services.notifier import send_whatsapp_message
from dotenv import load_dotenv

load_dotenv()

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")


def generate_daily_report(db: Session, report_type: str = "morning") -> str:
    """
    Generate consolidated order report.
    report_type: 'morning' (5am) or 'evening' (6pm)
    """

    today = date.today()

    # Get all orders for today
    orders = db.query(Order).filter(
        Order.created_at >= datetime.combine(today, datetime.min.time()),
        Order.created_at <= datetime.combine(today, datetime.max.time())
    ).order_by(Order.created_at.asc()).all()

    if not orders:
        return None

    # Separate clear and unclear orders
    clear_orders = [o for o in orders if not o.is_unclear]
    unclear_orders = [o for o in orders if o.is_unclear]

    # Aggregate all items across all orders
    # Aggregate all items across all orders
    product_totals = {}
    for order in clear_orders:
        items = json.loads(order.parsed_items) if order.parsed_items else []
        for item in items:
            product = item.get("product", "Unknown")
            quantity = item.get("quantity", 0)
            unit = item.get("unit", "kg")
            # Normalize product name
            normalized = product.strip()
            # Words that indicate product already has full name
            skip_prefix = [
                "chicken", "whole", "tandoor", 
                "spring", "half"
            ]
            # Only add Chicken prefix if none of skip words present
            if not any(normalized.lower().startswith(w) for w in skip_prefix):
                normalized = f"Chicken {normalized}"
            key = f"{normalized}||{unit}"
            product = normalized
            if key not in product_totals:
                product_totals[key] = {
                    "product": product,
                    "unit": unit,
                    "total_quantity": 0,
                    "orders_count": 0
                }
            product_totals[key]["total_quantity"] += quantity
            product_totals[key]["orders_count"] += 1

    # Build report header
    emoji = "🌅" if report_type == "morning" else "🌆"
    report_time = "Morning" if report_type == "morning" else "Evening"

    report = f"""{emoji} *{PLANT_NAME} — {report_time} Order Report*
📅 Date: {today.strftime('%d %B %Y')}
⏰ Generated: {datetime.now().strftime('%I:%M %p')}

━━━━━━━━━━━━━━━━━━━━
📦 *TOTAL ORDERS: {len(clear_orders)}*
━━━━━━━━━━━━━━━━━━━━

"""

    # Add consolidated product summary
    if product_totals:
        report += "*📊 CONSOLIDATED SUMMARY:*\n"
        for key, data in product_totals.items():
            report += f"• {data['product']} — *{data['total_quantity']} {data['unit']}*\n"

    report += "\n━━━━━━━━━━━━━━━━━━━━\n"

    # Add individual order details
    report += "\n*📋 ORDER DETAILS:*\n"
    for i, order in enumerate(clear_orders, 1):
        items = json.loads(order.parsed_items) if order.parsed_items else []
        delivery = ""
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

    # Add unclear orders section
    if unclear_orders:
        report += f"\n━━━━━━━━━━━━━━━━━━━━\n"
        report += f"⚠️ *UNCLEAR ORDERS: {len(unclear_orders)}*\n"
        report += "These need manual follow up:\n"
        for order in unclear_orders:
            report += f"• {order.customer_phone} — {order.raw_message[:50]}...\n"

    report += f"\n━━━━━━━━━━━━━━━━━━━━"
    report += f"\n_OrdeRR — Fluffy Plant Automation_"

    return report


def send_morning_report(db: Session):
    """Send 5am consolidated report to manager"""
    print(f"\n⏰ Generating 5AM Morning Report...")
    report = generate_daily_report(db, report_type="morning")
    if report:
        send_whatsapp_message(MANAGER_PHONE, report)
        print("✅ Morning report sent!")
    else:
        print("ℹ️ No orders found for today — report not sent")


def send_evening_report(db: Session):
    """Send 6pm consolidated report to manager"""
    print(f"\n⏰ Generating 6PM Evening Report...")
    report = generate_daily_report(db, report_type="evening")
    if report:
        send_whatsapp_message(MANAGER_PHONE, report)
        print("✅ Evening report sent!")
    else:
        print("ℹ️ No orders found for today — report not sent")