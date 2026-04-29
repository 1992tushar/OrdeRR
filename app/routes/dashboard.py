from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.order import Order
from datetime import datetime, date
import json
import os

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    """Main dashboard showing today's orders"""

    today = date.today()

    # Get today's orders
    orders = db.query(Order).filter(
        Order.created_at >= datetime.combine(today, datetime.min.time()),
        Order.created_at <= datetime.combine(today, datetime.max.time())
    ).order_by(Order.created_at.desc()).all()

    # Parse items for each order
    for order in orders:
        order.items_parsed = json.loads(order.parsed_items) if order.parsed_items else []

    # Separate clear and unclear orders
    clear_orders = [o for o in orders if not o.is_unclear]
    unclear_orders = [o for o in orders if o.is_unclear]

    # Build product summary
    product_summary = {}
    for order in clear_orders:
        for item in order.items_parsed:
            product = item.get("product", "Unknown")
            quantity = item.get("quantity", 0)
            unit = item.get("unit", "kg")
            key = f"{product}__{unit}"
            if key not in product_summary:
                product_summary[key] = {
                    "product": product,
                    "unit": unit,
                    "total_quantity": 0,
                    "orders_count": 0
                }
            product_summary[key]["total_quantity"] += quantity
            product_summary[key]["orders_count"] += 1

    plant_name = os.getenv("PLANT_NAME", "Fluffy")
    current_time = datetime.now().strftime("%d %b %Y, %I:%M %p")

    # Build product summary rows
    summary_rows = ""
    for key, data in product_summary.items():
        summary_rows += f"""
        <tr>
            <td>{data['product']}</td>
            <td><strong>{data['total_quantity']}</strong></td>
            <td>{data['unit']}</td>
            <td>{data['orders_count']}</td>
        </tr>"""

    if not summary_rows:
        summary_rows = "<tr><td colspan='4' style='text-align:center;color:#aaa;'>No orders yet today</td></tr>"

    # Build order cards
    order_cards = ""
    for order in clear_orders:
        delivery = ""
        if order.delivery_date and order.delivery_time:
            delivery = f"{order.delivery_date} at {order.delivery_time}"
        elif order.delivery_date:
            delivery = order.delivery_date
        else:
            delivery = "Not specified"

        items_html = ""
        for item in order.items_parsed:
            items_html += f"• {item['product']} — {item['quantity']} {item['unit']}<br>"

        order_cards += f"""
        <div class="order-card">
            <div class="order-header">
                <span class="phone">📱 {order.customer_phone}</span>
                <span class="delivery">🕐 {delivery}</span>
            </div>
            <div class="items">{items_html}</div>
        </div>"""

    if not order_cards:
        order_cards = "<div class='empty'>No clear orders yet today</div>"

    # Build unclear order cards
    unclear_cards = ""
    for order in unclear_orders:
        unclear_cards += f"""
        <div class="order-card unclear">
            <div class="order-header">
                <span class="phone">📱 {order.customer_phone}</span>
                <span class="badge unclear">unclear</span>
            </div>
            <div class="raw-message">"{order.raw_message}"</div>
            <div class="reason">❓ {order.unclear_reason or 'Unknown reason'}</div>
        </div>"""

    unclear_section = ""
    if unclear_orders:
        unclear_section = f"""
        <div class="section">
            <h2>⚠️ Unclear Orders — Needs Attention</h2>
            {unclear_cards}
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OrdeRR — {plant_name} Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', sans-serif; background: #f0f2f5; color: #333; }}
        .header {{ background: #075e54; color: white; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; }}
        .header h1 {{ font-size: 22px; }}
        .header span {{ font-size: 13px; opacity: 0.8; }}
        .cards {{ display: flex; gap: 16px; padding: 24px; flex-wrap: wrap; }}
        .card {{ background: white; border-radius: 12px; padding: 20px 24px; flex: 1; min-width: 150px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); text-align: center; }}
        .card .number {{ font-size: 36px; font-weight: bold; color: #075e54; }}
        .card .label {{ font-size: 13px; color: #888; margin-top: 4px; }}
        .card.warning .number {{ color: #e67e22; }}
        .section {{ padding: 0 24px 24px; }}
        .section h2 {{ font-size: 16px; color: #555; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 2px solid #075e54; }}
        .summary-table {{ background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.1); margin-bottom: 24px; }}
        .summary-table table {{ width: 100%; border-collapse: collapse; }}
        .summary-table th {{ background: #075e54; color: white; padding: 12px 16px; text-align: left; font-size: 13px; }}
        .summary-table td {{ padding: 12px 16px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }}
        .order-card {{ background: white; border-radius: 12px; padding: 16px 20px; margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); border-left: 4px solid #075e54; }}
        .order-card.unclear {{ border-left-color: #e67e22; }}
        .order-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
        .phone {{ font-weight: bold; font-size: 15px; }}
        .delivery {{ font-size: 12px; color: #888; }}
        .items {{ font-size: 13px; color: #555; line-height: 1.8; }}
        .raw-message {{ font-size: 12px; color: #e67e22; margin-top: 8px; font-style: italic; }}
        .reason {{ font-size: 12px; color: #e67e22; margin-top: 4px; }}
        .badge {{ padding: 4px 10px; border-radius: 20px; font-size: 11px; font-weight: bold; }}
        .badge.unclear {{ background: #fff3e0; color: #e65100; }}
        .refresh-btn {{ background: #075e54; color: white; border: none; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 13px; margin-left: 12px; }}
        .empty {{ text-align: center; padding: 40px; color: #aaa; font-size: 14px; }}
    </style>
</head>
<body>
<div class="header">
    <div>
        <h1>🐔 OrdeRR — {plant_name}</h1>
        <span>Order Management Dashboard</span>
    </div>
    <div>
        <span>{current_time}</span>
        <button class="refresh-btn" onclick="location.reload()">🔄 Refresh</button>
    </div>
</div>

<div class="cards">
    <div class="card">
        <div class="number">{len(orders)}</div>
        <div class="label">Total Orders Today</div>
    </div>
    <div class="card">
        <div class="number">{len(clear_orders)}</div>
        <div class="label">Clear Orders</div>
    </div>
    <div class="card warning">
        <div class="number">{len(unclear_orders)}</div>
        <div class="label">Needs Attention</div>
    </div>
    <div class="card">
        <div class="number">{sum(len(o.items_parsed) for o in clear_orders)}</div>
        <div class="label">Total Items</div>
    </div>
</div>

<div class="section">
    <h2>📊 Product Summary</h2>
    <div class="summary-table">
        <table>
            <thead>
                <tr>
                    <th>Product</th>
                    <th>Total Quantity</th>
                    <th>Unit</th>
                    <th>Orders</th>
                </tr>
            </thead>
            <tbody>
                {summary_rows}
            </tbody>
        </table>
    </div>
</div>

<div class="section">
    <h2>✅ Today's Orders</h2>
    {order_cards}
</div>

{unclear_section}

</body>
</html>"""

    return HTMLResponse(content=html)