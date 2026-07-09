"""
app/routes/ledger.py
--------------------
Public customer ledger — no auth required, access via per-customer token.

GET /ledger/{token}   → HTML order history page (last 7 days)
"""

import os
from datetime import datetime, timezone, timedelta, date

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi import Request
from sqlalchemy.orm import Session

from orderr_core.database import get_db
from orderr_core.models.customer import Customer
from orderr_core.models.order import Order

import json

router = APIRouter()
from orderr_core.constants import IST
PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")
from orderr_core.templating import make_templates
templates = make_templates()
LEDGER_DAYS = 7


from orderr_core.utils import safe_list as _safe_list


@router.get("/{token}", response_class=HTMLResponse)
def customer_ledger(token: str, request: Request, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.ledger_token == token).first()
    if not customer:
        return Response(content=_not_found_html(), media_type="text/html", status_code=404)

    now_ist = datetime.now(IST)
    cutoff = (now_ist - timedelta(days=LEDGER_DAYS)).date()
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    orders = (
        db.query(Order)
        .filter(
            Order.customer_phone == customer.phone_number,
            Order.is_cancelled == False,
            Order.business_date >= cutoff_str,
        )
        .order_by(Order.business_date.desc(), Order.created_at.desc())
        .all()
    )

    # Group by business_date
    grouped: dict[str, list] = {}
    for order in orders:
        bd = order.business_date or (
            order.created_at.astimezone(IST).strftime("%Y-%m-%d") if order.created_at else "Unknown"
        )
        if bd not in grouped:
            grouped[bd] = []
        items = _safe_list(order.parsed_items)
        unclear = _safe_list(order.unclear_items)
        grouped[bd].append({
            "id": order.id,
            "order_lines": items,
            "unclear_items": unclear,
            "delivery_time": order.delivery_time,
            "is_unclear": order.is_unclear,
            "created_at": order.created_at.astimezone(IST).strftime("%I:%M %p") if order.created_at else "",
        })

    days_data = []
    for bd_str, day_orders in sorted(grouped.items(), reverse=True):
        try:
            bd = date.fromisoformat(bd_str)
            today = now_ist.date()
            if bd == today:
                label = "Today"
            elif bd == today - timedelta(days=1):
                label = "Yesterday"
            else:
                label = bd.strftime("%A, %d %b")
        except ValueError:
            label = bd_str

        days_data.append({
            "date_str": bd_str,
            "label": label,
            "orders": day_orders,
        })

    return templates.TemplateResponse(
        request=request,
        name="ledger.html",
        context={
            "plant_name": PLANT_NAME,
            "customer_name": customer.restaurant_name or "Customer",
            "days": days_data,
            "total_orders": len(orders),
            "ledger_days": LEDGER_DAYS,
            "generated_at": now_ist.strftime("%d %b %Y, %I:%M %p"),
        },
    )


def _not_found_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Not Found</title>
<style>
  body { font-family: 'Segoe UI', sans-serif; background: #f5f5f5;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; margin: 0; }
  .box { background: white; border-radius: 16px; padding: 40px 32px;
         text-align: center; max-width: 320px; box-shadow: 0 2px 16px rgba(0,0,0,.1); }
  h2 { color: #333; margin-bottom: 8px; }
  p  { color: #888; font-size: 14px; line-height: 1.6; }
</style>
</head>
<body>
  <div class="box">
    <div style="font-size:48px;margin-bottom:16px">🔗</div>
    <h2>Link not found</h2>
    <p>This order history link is invalid or has expired.<br>
       Please message us to get a fresh link.</p>
  </div>
</body>
</html>"""
