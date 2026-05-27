from fastapi import APIRouter, Depends, Request, Query
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
IST = timezone(timedelta(hours=5, minutes=30))
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    view_date: str = Query(default=None, description="YYYY-MM-DD to view past orders"),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    # Determine which date to show
    today = datetime.now(IST).date()
    if view_date:
        try:
            target_date = date.fromisoformat(view_date)
        except ValueError:
            target_date = today
    else:
        target_date = today

    orders = db.query(Order).filter(
        func.date(Order.created_at) == target_date,
        Order.is_cancelled == False,
        Order.status.notin_(["pending_replace", "pending_repeat"]),
    ).order_by(Order.created_at.desc()).all()

    for order in orders:
        order.items_parsed = json.loads(order.parsed_items) if order.parsed_items else []

    clear_orders   = [o for o in orders if not o.is_unclear]
    unclear_orders = [o for o in orders if o.is_unclear]

    product_summary = {}
    for order in clear_orders:
        for item in order.items_parsed:
            product  = item.get("product", "Unknown")
            quantity = item.get("quantity", 0)
            unit     = item.get("unit", "kg").lower()  # normalize KG → kg for display
            key      = f"{product}__{unit}"
            if key not in product_summary:
                product_summary[key] = {"product": product, "unit": unit, "total_quantity": 0, "orders_count": 0}
            product_summary[key]["total_quantity"] += quantity
            product_summary[key]["orders_count"]   += 1

    # Date navigation helpers
    yesterday = (target_date - timedelta(days=1)).isoformat()
    tomorrow  = (target_date + timedelta(days=1)).isoformat()
    is_today  = (target_date == today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "plant_name"         : os.getenv("PLANT_NAME", "Fluffy"),
            "current_time"       : datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "orders"             : orders,
            "clear_orders"       : clear_orders,
            "unclear_orders"     : unclear_orders,
            "product_summary"    : list(product_summary.values()),
            "total_items"        : sum(len(o.items_parsed) for o in clear_orders),
            "target_date"        : target_date.isoformat(),
            "target_date_display": target_date.strftime("%d %b %Y"),
            "is_today"           : is_today,
            "yesterday"          : yesterday,
            "tomorrow"           : tomorrow,
            "dashboard_username" : os.getenv("DASHBOARD_USERNAME", ""),
            "dashboard_password" : os.getenv("DASHBOARD_PASSWORD", ""),
        },
    )
