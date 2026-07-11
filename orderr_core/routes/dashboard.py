from fastapi import APIRouter, Depends, Request, Query, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from orderr_core.database import get_db
from orderr_core.models.order import Order
from orderr_core.auth import require_auth
from datetime import datetime, date, timezone, timedelta
from orderr_core.services.order_service import get_current_business_date, group_orders_by_area
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

    # Group clear orders by the customer's assigned area (busiest area first,
    # "Area not set" last) so the board reads route-by-route.
    area_groups = group_orders_by_area(db, clear_orders)

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
            "area_groups"        : area_groups,
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
    forecast = analytics_service.demand_forecast(db, today)

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
            "forecast"   : forecast,
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


@router.get("/analytics/digest")
def analytics_digest_preview(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P3-6 — preview the manager daily digest text (what gets WhatsApp'd)."""
    from orderr_core.services import analytics_service
    from orderr_core.config import MANAGER_PHONE

    today = get_current_business_date()
    digest = analytics_service.manager_digest(db, today)
    return JSONResponse({"status": "ok", "manager_phone_set": bool(MANAGER_PHONE),
                         "digest": digest})


@router.post("/analytics/digest/send")
def analytics_digest_send(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P3-6 — send the manager digest now (manual trigger)."""
    from orderr_core.services.reporter import send_manager_digest
    sent = send_manager_digest(db)
    return JSONResponse({"status": "ok" if sent else "skipped",
                         "sent": sent})


@router.get("/analytics/chase", response_class=HTMLResponse)
def analytics_chase(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P3-7 — collection chase list ("call today")."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    data = analytics_service.chase_list(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_chase.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "ch"         : data,
            "analytics_view": "chase",
        },
    )


@router.get("/analytics/credit", response_class=HTMLResponse)
def analytics_credit(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P3-1/2/4/5 — credit intelligence: risk score, classification, at-risk,
    breach."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    data = analytics_service.credit_intelligence(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_credit.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "ci"         : data,
            "analytics_view": "credit",
        },
    )


@router.get("/analytics/financials", response_class=HTMLResponse)
def analytics_financials(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P3-11/12/13/14 — plant P&L, cash-flow, gross margin, payables."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    data = analytics_service.plant_financials(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_financials.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "fin"        : data,
            "analytics_view": "financials",
        },
    )


@router.get("/analytics/reconcile", response_class=HTMLResponse)
def analytics_reconcile(
    request: Request,
    date: str = Query(default=None, description="YYYY-MM-DD (defaults to latest Vasy invoice date)"),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P2-16 — OrdeRR↔Vasy invoice reconciliation (billing leakage)."""
    from datetime import date as _date
    from orderr_core.services import analytics_service

    target = None
    if date:
        try:
            target = _date.fromisoformat(date)
        except ValueError:
            target = None
    data = analytics_service.reconciliation(db, target_date=target)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_reconcile.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "rc"         : data,
            "analytics_view": "reconcile",
        },
    )


@router.get("/analytics/collections", response_class=HTMLResponse)
def analytics_collections(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P2-10 collection velocity + P2-4 unattributed receipts."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    data = analytics_service.collections(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_collections.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "col"        : data,
            "analytics_view": "collections",
        },
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


@router.get("/analytics/admin/diagnose-matching")
def analytics_diagnose_matching(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Read-only: why are Vasy invoices/receipts unmatched to customers?
    Compares customer names to the unmatched invoice/receipt party names so we
    can see if it's name divergence, genuine non-customers, or something else."""
    from sqlalchemy import func
    from orderr_core.models.customer import Customer
    from orderr_core.models.vasy_invoice import VasyInvoice
    from orderr_core.models.customer_receipt import CustomerReceipt

    customers = db.query(Customer).all()

    def unmatched_report(model, party_col, amt_col, limit=25):
        # Just the facts: unmatched party names, count, ₹. No fuzzy guessing —
        # matching is exact (name/phone/outstanding link), never similarity.
        rows = (db.query(party_col, func.count(model.id), func.coalesce(func.sum(amt_col), 0))
                .filter(model.customer_id == None)                     # noqa: E711
                .group_by(party_col)
                .order_by(func.sum(amt_col).desc()).limit(limit).all())
        return [{"party": p, "count": int(n), "total": round(float(t), 2)} for p, n, t in rows]

    inv_total = db.query(func.count(VasyInvoice.id)).scalar()
    inv_unmatched = db.query(func.count(VasyInvoice.id)).filter(VasyInvoice.customer_id == None).scalar()  # noqa: E711
    rec_total = db.query(func.count(CustomerReceipt.id)).scalar()
    rec_unmatched = db.query(func.count(CustomerReceipt.id)).filter(CustomerReceipt.customer_id == None).scalar()  # noqa: E711

    # customers that never got an invoice matched (their name isn't on any invoice)
    matched_cust_ids = {cid for (cid,) in db.query(VasyInvoice.customer_id).distinct()
                        .filter(VasyInvoice.customer_id != None).all()}  # noqa: E711
    cust_no_invoice = [c.restaurant_name for c in customers if c.id not in matched_cust_ids]

    return JSONResponse({
        "customers_total": len(customers),
        "sample_customer_names": sorted([c.restaurant_name or "" for c in customers])[:25],
        "invoices_total": inv_total, "invoices_unmatched": inv_unmatched,
        "receipts_total": rec_total, "receipts_unmatched": rec_unmatched,
        "customers_with_no_matched_invoice": len(cust_no_invoice),
        "sample_customers_no_invoice": sorted(cust_no_invoice)[:25],
        "top_unmatched_invoice_parties": unmatched_report(VasyInvoice, VasyInvoice.party_name, VasyInvoice.total),
        "top_unmatched_receipt_parties": unmatched_report(CustomerReceipt, CustomerReceipt.party_name, CustomerReceipt.amount),
    })


@router.post("/analytics/admin/reset")
def analytics_admin_reset(
    request: Request,
    token: str = Query(default=None),
    confirm: str = Query(default="false"),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Token-gated destructive reset: clears transactional + analytics data,
    PRESERVES customers/salespersons/staff.

    Safety: disabled unless RESET_TOKEN env var is set; requires that token;
    dry-run (returns counts) unless confirm=true. Explicit clear-list only.
    """
    import os
    import secrets
    from orderr_core.services import maintenance

    reset_token = os.getenv("RESET_TOKEN", "")
    if not reset_token:
        raise HTTPException(status_code=403, detail="Reset disabled — RESET_TOKEN is not set on the server.")
    if not token or not secrets.compare_digest(token, reset_token):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")

    do = (confirm == "true")
    result = maintenance.reset_transactional_data(db, confirm=do)
    tables = result["tables"]
    return JSONResponse({
        "status": "DELETED" if do else "dry-run",
        "confirm": do,
        "preserved_tables": maintenance.PRESERVE_TABLES,
        ("deleted" if do else "would_delete"): tables,
        "total_rows": sum(tables.values()),
    })


@router.get("/analytics/imports", response_class=HTMLResponse)
def analytics_imports(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P2-5 — manual Vasy file upload + import history + data coverage."""
    from orderr_core.models.import_log import ImportLog
    from orderr_core.services import analytics_service

    coverage = analytics_service.import_coverage(db, get_current_business_date())
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
            "coverage"   : coverage,
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


@router.post("/analytics/import/sales-invoices")
async def analytics_import_sales_invoices(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Upload a Vasy sales-invoice export → VasyInvoice/VasyInvoiceItem."""
    from orderr_core.services import vasy_import

    fname = (file.filename or "").lower()
    if not fname.endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Please upload the sales-invoice .xlsx export.")
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    try:
        summary = vasy_import.import_sales_invoices(db, contents, source_file=file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse({"status": "ok", "summary": summary})


async def _import_cost_file(entity, importer, file, db):
    fname = (file.filename or "").lower()
    if not fname.endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail=f"Please upload the {entity} .xlsx export.")
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    try:
        summary = importer(db, contents, source_file=file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse({"status": "ok", "summary": summary})


@router.post("/analytics/import/sales-items")
async def analytics_import_sales_items(file: UploadFile = File(...), db: Session = Depends(get_db),
                                       username: str = Depends(require_auth)):
    """Upload a Vasy Sales & Sales Return Item Register (line-item SKU sales)."""
    from orderr_core.services import vasy_import
    return await _import_cost_file("sales items", vasy_import.import_sales_items, file, db)


@router.post("/analytics/import/purchases")
async def analytics_import_purchases(file: UploadFile = File(...), db: Session = Depends(get_db),
                                     username: str = Depends(require_auth)):
    """Upload a Vasy purchase export (P3-10)."""
    from orderr_core.services import vasy_import
    return await _import_cost_file("purchases", vasy_import.import_purchases, file, db)


@router.post("/analytics/import/expenses")
async def analytics_import_expenses(file: UploadFile = File(...), db: Session = Depends(get_db),
                                    username: str = Depends(require_auth)):
    """Upload a Vasy expense export (P3-10)."""
    from orderr_core.services import vasy_import
    return await _import_cost_file("expenses", vasy_import.import_expenses, file, db)


@router.post("/analytics/import/payments")
async def analytics_import_payments(file: UploadFile = File(...), db: Session = Depends(get_db),
                                    username: str = Depends(require_auth)):
    """Upload a Vasy payment export (P3-10)."""
    from orderr_core.services import vasy_import
    return await _import_cost_file("payments", vasy_import.import_payments, file, db)


@router.post("/analytics/import/supplier-outstanding")
async def analytics_import_supplier_bills(file: UploadFile = File(...), db: Session = Depends(get_db),
                                          username: str = Depends(require_auth)):
    """Upload a Vasy Supplier Bill List (accounts payable)."""
    from orderr_core.services import vasy_import
    return await _import_cost_file("supplier outstanding", vasy_import.import_supplier_bills, file, db)


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


@router.get("/analytics/portfolio", response_class=HTMLResponse)
def analytics_portfolio(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Phase 4.2 — Value × Risk portfolio map."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    data = analytics_service.portfolio(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_portfolio.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "today_display": today.strftime("%d %b %Y"),
            "pf"         : data,
            "analytics_view": "portfolio",
        },
    )


@router.get("/analytics/payment-behaviour", response_class=HTMLResponse)
def analytics_payment_behaviour(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Phase 4.3 — payment behaviour: DSO, concentration, early warnings."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    data = analytics_service.payment_behaviour(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_payment.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "today_display": today.strftime("%d %b %Y"),
            "pb"         : data,
            "analytics_view": "payment",
        },
    )


@router.get("/analytics/lifecycle", response_class=HTMLResponse)
def analytics_lifecycle(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Phase 4.4 — lifecycle stages, spend movers, acquisition cohorts."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    data = analytics_service.lifecycle_cohorts(db, today)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_lifecycle.html",
        context={
            "plant_name" : PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "today_display": today.strftime("%d %b %Y"),
            "lc"         : data,
            "analytics_view": "lifecycle",
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


@router.post("/analytics/customer/{customer_id}/credit-limit")
async def analytics_set_credit_limit(
    request: Request,
    customer_id: int,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """P3-3 — set/clear a customer's credit limit (₹). Empty/blank clears it."""
    from decimal import Decimal, InvalidOperation
    from orderr_core.models.customer import Customer

    customer = db.query(Customer).get(customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    body = await request.json()
    raw = body.get("credit_limit")
    if raw in (None, "", "null"):
        customer.credit_limit = None
    else:
        try:
            val = Decimal(str(raw).replace(",", "").strip())
        except (InvalidOperation, ValueError):
            raise HTTPException(status_code=400, detail="Invalid credit limit amount.")
        if val < 0:
            raise HTTPException(status_code=400, detail="Credit limit cannot be negative.")
        customer.credit_limit = val
    db.commit()
    return JSONResponse({"status": "ok",
                         "credit_limit": (float(customer.credit_limit)
                                          if customer.credit_limit is not None else None)})


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
