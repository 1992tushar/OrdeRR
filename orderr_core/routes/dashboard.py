from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from orderr_core.database import get_db
from orderr_core.models.order import Order
from orderr_core.auth import require_auth
from datetime import datetime, date, timezone, timedelta
from orderr_core.services.order_service import get_current_business_date
from orderr_core.config import PLANT_NAME
import json
import os

router = APIRouter()
from orderr_core.constants import IST
from orderr_core.templating import make_templates
templates = make_templates()

from orderr_core.utils import safe_list as _safe_list


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    view_date: str = Query(default=None, description="YYYY-MM-DD to view past orders"),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    today = get_current_business_date()
    if view_date:
        try:
            target_date = date.fromisoformat(view_date)
        except ValueError:
            target_date = today
    else:
        target_date = today

    orders = db.query(Order).filter(
        Order.business_date == target_date.strftime("%Y-%m-%d"),
        Order.is_cancelled == False,
    ).all()

    for order in orders:
        order.created_at = order.created_at.astimezone(IST)
        order.items_parsed = _safe_list(order.parsed_items)
        unclear_list = _safe_list(order.unclear_items)
        order.has_unclear_items = bool(unclear_list)
        order.unclear_items_list = unclear_list

    clear_orders   = [o for o in orders if not o.is_unclear]
    unclear_orders = [o for o in orders if o.is_unclear]

    product_summary = {}
    for order in clear_orders:
        for item in order.items_parsed:
            if not isinstance(item, dict):
                continue
            product  = item.get("product", "Unknown")
            quantity = item.get("quantity", 0)
            unit     = item.get("unit", "kg").lower()
            key      = f"{product}__{unit}"
            if key not in product_summary:
                product_summary[key] = {"product": product, "unit": unit, "total_quantity": 0, "orders_count": 0}
            product_summary[key]["total_quantity"] += quantity
            product_summary[key]["orders_count"]   += 1

    yesterday = (target_date - timedelta(days=1)).isoformat()
    tomorrow  = (target_date + timedelta(days=1)).isoformat()
    is_today  = (target_date == today)
    now_ist = datetime.now(IST)
    is_before_cutoff = now_ist.hour < 20

    failed_messages   = []
    reliability_stats = {"has_issues": False, "total_today": 0, "confirmed_today": 0, "failed_today": 0, "manual_review_total": 0}

    try:
        from orderr_core.models.inbound_message import InboundMessage
        from orderr_core.services.message_journal import get_reliability_stats, get_all_failed_messages

        reliability_stats = get_reliability_stats(db)

        for m in get_all_failed_messages(db, limit=100):
            failed_messages.append({
                "id":                m.id,
                "customer_phone":    m.customer_phone,
                "raw_message":       (m.raw_message or "")[:400],
                "received_at":       m.received_at.strftime("%d %b %Y %I:%M %p") if m.received_at else "",
                "processing_status": m.processing_status,
                "failure_reason":    m.failure_reason or "Unknown error",
                "attempts":          m.processing_attempts,
                "ack_failed":        m.ack_failed,
                "linked_order_id":   m.linked_order_id,
            })
    except Exception:
        pass

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "plant_name"         : PLANT_NAME,
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
            "is_before_cutoff"   : is_before_cutoff,
            "tomorrow"           : tomorrow,
            "failed_messages"    : failed_messages,
            "reliability_stats"  : reliability_stats,
        },
    )


@router.get("/analytics", response_class=HTMLResponse)
def analytics(
    request: Request,
    c360_days: str = Query(default="30", description="Customer-360 window: 7|30|90|all"),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Analytics home — sales/ops insight layer (Phase 1).

    Sales figures here come from OrdeRR's own (operational) invoices; Vasy
    remains the money source-of-truth once the Phase-2 mirror lands.
    """
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    pulse = analytics_service.business_pulse(db, today)

    days = analytics_service.C360_WINDOWS.get(c360_days, 30)
    c360 = analytics_service.customer_360(db, today, days=days)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "today_display": today.strftime("%d %b %Y"),
            "pulse"      : pulse,
            "c360"       : c360,
            "c360_days"  : c360_days,
        },
    )
