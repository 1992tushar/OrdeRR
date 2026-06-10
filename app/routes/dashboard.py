from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models.order import Order
from app.auth import require_auth
from datetime import datetime, date, timezone, timedelta
from app.services.order_service import get_current_business_date
import json
import os

router = APIRouter()
IST = timezone(timedelta(hours=5, minutes=30))
templates = Jinja2Templates(directory="app/templates")


def _safe_list(value) -> list:
    """
    Return a guaranteed list from a JSONB field.
    Handles: None, already-a-list (normal JSONB), JSON string (legacy),
    double-encoded string, empty/null sentinels.
    """
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        if value in ("null", "[]", ""):
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
            # double-encoded: parsed is still a string
            if isinstance(parsed, str):
                inner = json.loads(parsed)
                return inner if isinstance(inner, list) else []
        except Exception:
            pass
    return []


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
        from app.models.inbound_message import InboundMessage
        from app.services.message_journal import get_reliability_stats, get_all_failed_messages

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
            "is_before_cutoff"   : is_before_cutoff,
            "tomorrow"           : tomorrow,
            "failed_messages"    : failed_messages,
            "reliability_stats"  : reliability_stats,
        },
    )