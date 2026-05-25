from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.order import Order
from app.auth import require_auth
from datetime import datetime, date, timezone, timedelta
import json
import os

router = APIRouter()

# IST = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

# Jinja2 auto-escapes all variables — eliminates XSS
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """Main dashboard showing today's orders. Requires Basic Auth."""

    today = date.today()

    # FIX: Use func.date() — works on both SQLite and PostgreSQL
    orders = db.query(Order).filter(
        func.date(Order.created_at) == today
    ).order_by(Order.created_at.desc()).all()

    # Parse items for each order
    for order in orders:
        order.items_parsed = (
            json.loads(order.parsed_items)
            if order.parsed_items
            else []
        )

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

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "plant_name": os.getenv("PLANT_NAME", "Fluffy"),
            # FIX: Use IST timezone instead of server local time
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "orders": orders,
            "clear_orders": clear_orders,
            "unclear_orders": unclear_orders,
            "product_summary": list(product_summary.values()),
            "total_items": sum(len(o.items_parsed) for o in clear_orders),
            "dashboard_username": os.getenv("DASHBOARD_USERNAME", ""),
            "dashboard_password": os.getenv("DASHBOARD_PASSWORD", ""),
        }
    )