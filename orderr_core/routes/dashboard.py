from fastapi import APIRouter, Depends, Request, Query, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
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
    money = analytics_service.money_pulse(db, today)

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
            "money"      : money,
            "c360"       : c360,
            "c360_days"  : c360_days,
            "analytics_view": "overview",
        },
    )


@router.get("/analytics/churn", response_class=HTMLResponse)
def analytics_churn(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P1-4 — silent-churn detector: customers overdue vs their own cadence."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    churn = analytics_service.churn_risk(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_churn.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "today_display": today.strftime("%d %b %Y"),
            "churn"      : churn,
            "analytics_view": "churn",
        },
    )


@router.get("/analytics/revenue", response_class=HTMLResponse)
def analytics_revenue(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P1-5 — revenue over time (overall + per-customer MoM)."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    rev = analytics_service.revenue_trends(db, today)
    growth = analytics_service.new_vs_lost(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_revenue.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "today_display": today.strftime("%d %b %Y"),
            "rev"        : rev,
            "growth"     : growth,
            "analytics_view": "revenue",
        },
    )


@router.get("/analytics/products", response_class=HTMLResponse)
def analytics_products(
    request: Request,
    mix_days: str = Query(default="30", description="Product-mix window: 7|30|90|all"),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P1-7 product mix (billed value/volume) + P1-8 demand trend (ordered)."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    days = analytics_service.C360_WINDOWS.get(mix_days, 30)
    mix = analytics_service.product_mix(db, today, days=days)
    demand = analytics_service.demand_trend(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_products.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "today_display": today.strftime("%d %b %Y"),
            "mix"        : mix,
            "mix_days"   : mix_days,
            "demand"     : demand,
            "analytics_view": "products",
        },
    )


@router.get("/analytics/export/{name}")
def analytics_export(
    request: Request,
    name: str,
    days: str = Query(default=None, description="Optional window: 7|30|90|all"),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P1-12 — download any analytics list as .xlsx."""
    from fastapi import HTTPException
    from fastapi.responses import Response
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    window = analytics_service.C360_WINDOWS.get(days, None) if days else None
    result = analytics_service.export_dataset(db, today, name, days=window)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown export")

    filename, sheet, headers, rows = result
    content = analytics_service.build_xlsx(sheet, headers, rows)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/analytics/receivables", response_class=HTMLResponse)
def analytics_receivables(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P2-8/9/11/12 — receivables: exposure, top debtors, days-since-payment,
    balance direction, aging proxy."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    data = analytics_service.receivables(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_receivables.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "rec"        : data,
            "analytics_view": "receivables",
        },
    )


@router.get("/analytics/imports", response_class=HTMLResponse)
def analytics_imports(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P2-5 — manual Vasy file upload + import history."""
    from orderr_core.models.import_log import ImportLog

    logs = db.query(ImportLog).order_by(ImportLog.imported_at.desc()).limit(25).all()
    rows = [{
        "entity": l.entity,
        "source_file": l.source_file or "",
        "rows_total": l.rows_total,
        "created": l.created,
        "updated": l.updated,
        "unmatched": l.unmatched,
        "notes": l.notes or "",
        "imported_at": l.imported_at.astimezone(IST).strftime("%d %b %Y %I:%M %p") if l.imported_at else "",
    } for l in logs]

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_imports.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "logs"       : rows,
            "analytics_view": "imports",
        },
    )


@router.post("/analytics/import/receipts")
async def analytics_import_receipts(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Upload a Vasy receipt export → CustomerReceipt (idempotent)."""
    from orderr_core.services import vasy_import

    fname = (file.filename or "").lower()
    if not fname.endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Please upload the receipt .xlsx export.")
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    try:
        summary = vasy_import.import_receipts(db, contents, source_file=file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse({"status": "ok", "summary": summary})


@router.post("/analytics/import/outstanding")
async def analytics_import_outstanding(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Upload a Vasy outstanding export → refresh customers + daily snapshot."""
    from orderr_core.services import vasy_import

    fname = (file.filename or "").lower()
    if not fname.endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Please upload the outstanding .xlsx export.")
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    try:
        summary = vasy_import.import_outstanding(db, contents, source_file=file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse({"status": "ok", "summary": summary})


@router.get("/analytics/rfm", response_class=HTMLResponse)
def analytics_rfm(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P1-13 — RFM segmentation."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    data = analytics_service.rfm(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_rfm.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "today_display": today.strftime("%d %b %Y"),
            "rfm"        : data,
            "analytics_view": "rfm",
        },
    )


@router.get("/analytics/team", response_class=HTMLResponse)
def analytics_team(
    request: Request,
    team_days: str = Query(default="30", description="Window: 7|30|90|all"),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P1-11 — salesperson & area performance (sales)."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    days = analytics_service.C360_WINDOWS.get(team_days, 30)
    team = analytics_service.team_performance(db, today, days=days)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_team.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "today_display": today.strftime("%d %b %Y"),
            "team"       : team,
            "team_days"  : team_days,
            "analytics_view": "team",
        },
    )


@router.get("/analytics/quality", response_class=HTMLResponse)
def analytics_quality(
    request: Request,
    fill_days: str = Query(default="90", description="Fill-rate window: 7|30|90|all"),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P1-9 fill rate (ordered vs delivered) + P1-10 parse-quality monitor."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    days = analytics_service.C360_WINDOWS.get(fill_days, 90)
    fill = analytics_service.fill_rate(db, today, days=days)
    parse = analytics_service.parse_quality(db, today, days=30)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_quality.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "today_display": today.strftime("%d %b %Y"),
            "fill"       : fill,
            "fill_days"  : fill_days,
            "parse"      : parse,
            "analytics_view": "quality",
        },
    )


@router.get("/analytics/customer/{customer_id}", response_class=HTMLResponse)
def analytics_customer(
    request: Request,
    customer_id: int,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P1-3 — single-customer sales detail (opens from a Customer 360 row)."""
    from fastapi import HTTPException
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    detail = analytics_service.customer_detail(db, customer_id, today)
    if detail is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_customer.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "d"          : detail,
        },
    )
