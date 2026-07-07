"""
routes/billing.py  (refactored — orders now sourced directly from orderr-core)

Key change: billing no longer waits for an HTML "production report" upload to
learn what was ordered today. orderr-core already writes a row to `orders`
the moment a customer places an order, with:
    customer_phone   -- reliable join key (indexed)
    customer_name    -- hotel name as known to orderr-core
    parsed_items     -- [{"product","quantity","unit"}, ...]
    unclear_items    -- ["raw text the AI parser could not resolve", ...]
    business_date    -- "YYYY-MM-DD" string
    is_cancelled / status

Billing's job is now:
  1. Read today's orders straight from `orders` (no upload step).
  2. Lazily seed `OrderItemActual` rows from `parsed_items` the first time an
     order is viewed (this replaces the old HTML-report-driven seeding).
  3. Surface `unclear_items` as review items (same UI/table as OCR-unmatched
     lines from photo uploads), tagged with a distinct reason.
  4. Photo upload only *matches* OCR'd hotel blocks against TODAY'S EXISTING
     orders (by customer_name) and writes delivered quantities -- it never
     creates a new order row. If no match is found, that hotel surfaces as
     an explicit error rather than silently creating a duplicate order.
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from orderr_core.database import get_db
from orderr_core.models.actuals import OrderItemActual
from orderr_core.models.daily_rate import DailyRate
from orderr_core.models.invoice import Invoice
from orderr_core.models.ocr_unmatched import OcrUnmatchedLine
from orderr_core.services.invoice_generator import (
    InvoiceAlreadyExistsError,
    InvoiceHoldError,
    generate_invoice,
)
from orderr_core.services.invoice_pdf import generate_invoice_pdf
from orderr_core.services.ocr_actuals_parser import parse_claude_hotel_rows
from orderr_core.services.ocr_engine import OCREngineError, get_production_report_engine
from orderr_core.services.rate_lookup import get_rate
from orderr_core.services.rate_parser import ACTIVE_PRODUCTS
from orderr_core.models.rate_override import CustomerRateOverride

import json
logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="orderr_core/templates")

_today = date.today

ORDER_TIME_UNCLEAR_REASON = "Unclear at order time (could not parse product/quantity)"


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@router.get("/billing", response_class=HTMLResponse)
def billing_home(request: Request):
    return templates.TemplateResponse(request, "billing.html", {})


# ---------------------------------------------------------------------------
# customer-rates (unchanged)
# ---------------------------------------------------------------------------

@router.get("/billing/api/customer-rates")
def api_customer_rates(db: Session = Depends(get_db)):
    today = _today()

    customers_raw = db.execute(
        text("""
            SELECT phone_number, restaurant_name
            FROM customers
            WHERE is_active = TRUE
            ORDER BY restaurant_name
        """)
    ).fetchall()
    customers = [{"phone": r[0], "name": r[1]} for r in customers_raw]

    product_rates = []
    for display_name, default_unit in ACTIVE_PRODUCTS:
        rr = get_rate(db, display_name, today)
        product_rates.append({
            "product":   display_name,
            "unit":      rr.unit or default_unit,
            "rate":      rr.rate_per_unit,
            "stale":     rr.not_confirmed_today,
            "rate_date": rr.rate_date.isoformat() if rr.rate_date else None,
        })

    overrides_raw = db.execute(
        text("""
            SELECT customer_phone, product, rate_per_unit
            FROM customer_rate_overrides
            WHERE effective_to IS NULL
            ORDER BY customer_phone, product
        """)
    ).fetchall()

    customer_overrides = [
        {"phone": r[0], "product": r[1], "rate": float(r[2])}
        for r in overrides_raw
    ]

    return {
        "customers":          customers,
        "products":           product_rates,
        "today":              today.isoformat(),
        "customer_overrides": customer_overrides,
    }


# ---------------------------------------------------------------------------
# save-rates (unchanged)
# ---------------------------------------------------------------------------

@router.post("/billing/api/save-rates")
async def api_save_rates(request: Request, db: Session = Depends(get_db)):
    body  = await request.json()
    today = _today()
    saved = []

    for item in body.get("rates", []):
        product = (item.get("product") or "").strip()
        unit    = (item.get("unit") or "kg").strip()
        try:
            rate_value = float(item.get("rate") or 0)
        except (TypeError, ValueError):
            continue
        if not product or rate_value <= 0:
            continue

        existing = db.scalars(
            select(DailyRate).where(
                DailyRate.product == product,
                DailyRate.business_date == today,
            )
        ).first()
        if existing:
            existing.rate_per_unit = rate_value
            existing.unit          = unit
        else:
            db.add(DailyRate(
                product=product,
                business_date=today,
                rate_per_unit=rate_value,
                unit=unit,
                source="dashboard",
                created_by="billing_dashboard",
            ))
        saved.append(product)

    db.commit()
    return {"ok": True, "saved": saved}


@router.post("/billing/api/save-customer-rate")
async def api_save_customer_rate(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    customer_phone = (body.get("customer_phone") or "").strip()
    rates = body.get("rates") or []

    if not customer_phone:
        return JSONResponse(status_code=400, content={"error": "No customer selected"})

    today = _today()
    saved = []

    for item in rates:
        product = (item.get("product") or "").strip()
        unit    = (item.get("unit") or "kg").strip()
        try:
            rate_value = float(item.get("rate") or 0)
        except (TypeError, ValueError):
            continue
        if not product or rate_value <= 0:
            continue

        # Deactivate any currently-active override for this customer+product
        active = db.scalars(
            select(CustomerRateOverride).where(
                CustomerRateOverride.customer_phone == customer_phone,
                CustomerRateOverride.product == product,
                CustomerRateOverride.effective_to.is_(None),
            )
        ).all()
        for ov in active:
            ov.effective_to = today

        db.add(CustomerRateOverride(
            customer_phone=customer_phone,
            product=product,
            rate_per_unit=rate_value,
            unit=unit,
            effective_from=today,
            effective_to=None,
        ))
        saved.append(product)

    db.commit()
    return {"ok": True, "saved": saved}

# ---------------------------------------------------------------------------
# Seeding: turn an orderr-core order into OrderItemActual + OcrUnmatchedLine
# rows the first time it's viewed. Idempotent — safe to call every request.
# ---------------------------------------------------------------------------

def _ensure_order_seeded(db: Session, order_id: int, parsed_items: list, unclear_items: list) -> None:
    already_seeded = db.scalar(
        select(func.count()).select_from(OrderItemActual).where(OrderItemActual.order_id == order_id)
    )
    if already_seeded:
        return

    for it in (parsed_items or []):
        product = (it.get("product") or "").strip()
        if not product:
            continue
        try:
            qty = Decimal(str(it.get("quantity") or 0))
        except Exception:
            qty = Decimal("0")
        unit = (it.get("unit") or "kg").strip()

        db.add(OrderItemActual(
            order_id=order_id,
            product=product,
            ordered_quantity=qty,
            ordered_unit=unit,
            actual_quantity=None,
            actual_unit=unit,
            capture_source="orderr_core",
            confidence=None,
            confirmed_by=None,
            confirmed_at=None,
        ))

    # Already-seeded check above means this only ever runs once per order,
    # so it's safe to insert unclear_items here too without dup risk.
    already_has_unclear = db.scalar(
        select(func.count()).select_from(OcrUnmatchedLine).where(
            OcrUnmatchedLine.order_id == order_id,
            OcrUnmatchedLine.reason == ORDER_TIME_UNCLEAR_REASON,
        )
    )
    if not already_has_unclear:
        for raw in (unclear_items or []):
            raw_text = str(raw).strip()
            if not raw_text:
                continue
            db.add(OcrUnmatchedLine(
                order_id=order_id,
                raw_line=raw_text,
                reason=ORDER_TIME_UNCLEAR_REASON,
                resolved=False,
            ))

    db.commit()


# ---------------------------------------------------------------------------
# Merge photo-OCR results into an EXISTING order's actuals. Never creates an
# order — that already happened in orderr-core when the customer ordered.
# ---------------------------------------------------------------------------

def _merge_actuals(db: Session, order_id: int, matched: list, unmatched: list) -> dict:
    existing = {a.product: a for a in db.scalars(
        select(OrderItemActual).where(OrderItemActual.order_id == order_id)
    ).all()}

    added = replaced = skipped_manual = needs_review_count = 0

    for item in matched:
        product = item["product"]
        ex      = existing.get(product)

        if ex and ex.capture_source == "dashboard_manual":
            skipped_manual += 1
            continue

        confidence = "needs_review" if item["needs_review"] else "auto"
        if item["needs_review"]:
            needs_review_count += 1

        if ex:
            # Was seeded from orderr_core (ordered qty known) or a prior
            # photo_ocr pass -- update in place, keep the ordered_quantity.
            ex.actual_quantity = item["quantity"]
            ex.actual_unit     = item["unit"]
            ex.capture_source  = "photo_ocr"
            ex.confidence      = confidence
            ex.confirmed_by    = None
            ex.confirmed_at    = None
            replaced += 1
        else:
            # Product appeared in the photo but wasn't part of the original
            # order -- e.g. an add-on delivered on the day. Use the photo's
            # own quantity as both ordered & actual since we have no other
            # ordered-quantity source for it.
            db.add(OrderItemActual(
                order_id=order_id,
                product=product,
                ordered_quantity=item.get("ordered_quantity_hint") or item["quantity"],
                ordered_unit=item["unit"],
                actual_quantity=item["quantity"],
                actual_unit=item["unit"],
                capture_source="photo_ocr",
                confidence=confidence,
                confirmed_by=None,
                confirmed_at=None,
            ))
            added += 1

    for line in unmatched:
        db.add(OcrUnmatchedLine(
            order_id=order_id,
            raw_line=line["raw_line"],
            reason=line["reason"],
            resolved=False,
        ))

    return {
        "added":          added,
        "replaced":       replaced,
        "skipped_manual": skipped_manual,
        "needs_review":   needs_review_count,
        "unmatched":      len(unmatched),
    }


def _find_todays_order_by_name(db: Session, hotel_name: str, today: date) -> Optional[tuple]:
    """
    Match a photo-OCR'd hotel name against TODAY'S EXISTING orders only.
    Returns (order_id, customer_name, customer_phone) or None.
    Never creates a row -- if nothing matches, the caller must surface this
    as an error so a real order isn't silently duplicated.
    """
    return db.execute(
        text("""
            SELECT id, customer_name, customer_phone
            FROM orders
            WHERE business_date = :today
              AND is_cancelled = FALSE
              AND status != 'cancelled'
              AND LOWER(customer_name) LIKE LOWER(:pattern)
            ORDER BY id DESC
            LIMIT 1
        """),
        {"today": today.isoformat(), "pattern": f"%{hotel_name}%"},
    ).fetchone()


# ---------------------------------------------------------------------------
# Photo upload — match against today's existing orders, never create new ones
# ---------------------------------------------------------------------------

@router.post("/billing/api/upload")
async def api_upload(
    photos: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    today            = _today()
    all_hotel_blocks = []

    try:
        engine = get_production_report_engine()
        for photo in photos:
            image_bytes = await photo.read()
            if not image_bytes:
                continue
            blocks = engine.extract_rows(image_bytes)
            all_hotel_blocks.extend(blocks)
    except OCREngineError as e:
        return JSONResponse(status_code=422, content={"error": str(e)})
    except Exception as e:
        logger.exception("Unexpected OCR error")
        return JSONResponse(status_code=500, content={"error": f"Extraction failed: {e}"})

    if not all_hotel_blocks:
        return JSONResponse(
            status_code=422,
            content={"error": "No hotel orders found in the uploaded photo(s)."},
        )

    parsed     = parse_claude_hotel_rows(all_hotel_blocks)
    order_info: dict[str, dict] = {}

    for hotel in parsed["hotels"]:
        hotel_name = hotel["hotel_name"]
        matched    = hotel["matched"]
        unmatched  = hotel["unmatched"]

        match_row = _find_todays_order_by_name(db, hotel_name, today)
        if not match_row:
            order_info[hotel_name] = {
                "order_id": None,
                "merge":    None,
                "error":    f"No order placed today matches \"{hotel_name}\" — check spelling, "
                            f"or confirm this hotel actually ordered today before re-uploading.",
                "skipped_invoiced": False,
            }
            continue

        order_id = match_row[0]

        already_invoiced = db.scalar(
            select(func.count()).select_from(Invoice).where(Invoice.order_id == order_id)
        )
        if already_invoiced:
            order_info[hotel_name] = {
                "order_id": order_id,
                "merge":    None,
                "error":    None,
                "skipped_invoiced": True,
            }
            continue

        try:
            merge_summary = _merge_actuals(db, order_id, matched, unmatched)
            order_info[hotel_name] = {
                "order_id": order_id,
                "merge":    merge_summary,
                "error":    None,
                "skipped_invoiced": False,
            }
        except Exception as e:
            logger.exception("Failed processing hotel %r", hotel_name)
            db.rollback()
            order_info[hotel_name] = {
                "order_id": None,
                "merge":    None,
                "error":    str(e),
                "skipped_invoiced": False,
            }

    db.commit()

    hotels_out = []
    for hotel in parsed["hotels"]:
        hotel_name = hotel["hotel_name"]
        info       = order_info[hotel_name]

        if info["error"]:
            hotels_out.append({
                "hotel_name": hotel_name,
                "order_id":   info["order_id"],
                "error":      info["error"],
                "status":     "error",
            })
            continue

        hotels_out.append(_build_hotel_record(db, hotel_name, info["order_id"]))

    return {"hotels": hotels_out, "today": today.isoformat()}


# ---------------------------------------------------------------------------
# Build the dashboard record for one order. Seeds actuals/unclear-items from
# orderr-core data on first view.
# ---------------------------------------------------------------------------

def _build_hotel_record(db: Session, hotel_name: str, order_id: int) -> dict:
    order_row = db.execute(
        text("SELECT parsed_items, unclear_items, customer_phone FROM orders WHERE id = :oid"),
        {"oid": order_id},
    ).fetchone()
    parsed_items, unclear_items, order_phone = (order_row or (None, None, None))

    # parsed_items / unclear_items are stored as JSON text — deserialize if needed
    if isinstance(parsed_items, str):
        parsed_items = json.loads(parsed_items) if parsed_items else []
    if isinstance(unclear_items, str):
        unclear_items = json.loads(unclear_items) if unclear_items else []

    _ensure_order_seeded(db, order_id, parsed_items or [], unclear_items or [])

    existing_invoice = db.scalars(
        select(Invoice).where(Invoice.order_id == order_id)
    ).first()

    # The order already knows its customer — that is authoritative. Only fall
    # back to a name lookup if the order somehow has no phone on record. The old
    # code did the reverse (name LIKE first), which picked the wrong customer
    # when two restaurant names collided (e.g. "SAIRAT BIRYANI" also matched
    # "Sairat Biryani Ravet", and ORDER BY id DESC chose the wrong one).
    if order_phone:
        customer_phone = order_phone
    else:
        customer_row = db.execute(
            text("""
                SELECT phone_number FROM customers
                WHERE LOWER(restaurant_name) = LOWER(:name)
                  AND is_active = TRUE
                ORDER BY id DESC
                LIMIT 1
            """),
            {"name": hotel_name},
        ).fetchone()
        customer_phone = customer_row[0] if customer_row else ""

    actuals = db.scalars(
        select(OrderItemActual).where(OrderItemActual.order_id == order_id)
    ).all()

    items_out = []
    for a in actuals:
        items_out.append({
            "actual_id":     a.id,
            "product":       a.product,
            "ordered_qty":   float(a.ordered_quantity) if a.ordered_quantity is not None else None,
            "actual_qty":    float(a.actual_quantity)  if a.actual_quantity  is not None else None,
            "unit":          a.actual_unit or a.ordered_unit,
            "needs_review":  a.confidence == "needs_review" and not a.confirmed_by,
            "review_reason": None,
        })

    unmatched_lines = db.scalars(
        select(OcrUnmatchedLine).where(
            OcrUnmatchedLine.order_id == order_id,
            OcrUnmatchedLine.resolved == False,  # noqa
        )
    ).all()

    has_needs_review = any(i["needs_review"] for i in items_out)
    has_unmatched     = len(unmatched_lines) > 0
    all_actuals_null  = len(items_out) == 0 or all(i["actual_qty"] is None for i in items_out)

    if existing_invoice:
        status = "invoiced"
    elif all_actuals_null:
        # Order exists, items seeded, but no delivery (photo) confirmation yet.
        status = "pending"
    elif has_needs_review or has_unmatched:
        status = "unclear"
    else:
        status = "clear"

    return {
        "hotel_name":     hotel_name,
        "order_id":       order_id,
        "customer_phone": customer_phone,
        "status":         status,
        "invoice_number": existing_invoice.invoice_number if existing_invoice else None,
        "invoice_id":     existing_invoice.id             if existing_invoice else None,
        "invoice_total":  float(existing_invoice.total)   if existing_invoice else None,
        "items":          items_out,
        "unmatched": [
            {"id": u.id, "raw_line": u.raw_line, "reason": u.reason}
            for u in unmatched_lines
        ],
    }


# ---------------------------------------------------------------------------
# today-results — now the single source of truth, straight off orderr-core
# ---------------------------------------------------------------------------

@router.get("/billing/api/today-results")
def api_today_results(db: Session = Depends(get_db)):
    today = _today()

    order_rows = db.execute(
        text("""
            SELECT id, customer_name FROM orders
            WHERE business_date = :today
              AND is_cancelled = FALSE
              AND status != 'cancelled'
            ORDER BY id
        """),
        {"today": today.isoformat()},
    ).fetchall()

    if not order_rows:
        return {
            "hotels":          [],
            "today":           today.isoformat(),
            "total_hotels":    0,
            "delivered_count": 0,
            "pending_count":   0,
        }

    hotels_out      = [_build_hotel_record(db, hotel_name=row[1], order_id=row[0]) for row in order_rows]
    total_hotels    = len(hotels_out)
    pending_count   = sum(1 for h in hotels_out if h["status"] == "pending")
    delivered_count = total_hotels - pending_count

    return {
        "hotels":          hotels_out,
        "today":           today.isoformat(),
        "total_hotels":    total_hotels,
        "delivered_count": delivered_count,
        "pending_count":   pending_count,
    }


# ---------------------------------------------------------------------------
# fix-item (unchanged)
# ---------------------------------------------------------------------------

@router.post("/billing/api/fix-item")
async def api_fix_item(request: Request, db: Session = Depends(get_db)):
    body          = await request.json()
    actual_id     = body.get("actual_id")
    confirmed_by  = (body.get("confirmed_by") or "plant_manager").strip()
    corrected_qty = body.get("actual_qty")

    actual = db.get(OrderItemActual, actual_id)
    if not actual:
        return JSONResponse(status_code=404, content={"error": "Item not found"})

    if corrected_qty is not None:
        try:
            actual.actual_quantity = Decimal(str(corrected_qty))
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid quantity"})

    actual.confidence   = "auto"
    actual.confirmed_by = confirmed_by
    actual.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# resolve-unmatched — now also resolves order-time unclear_items, not just OCR
# ---------------------------------------------------------------------------

@router.post("/billing/api/resolve-unmatched")
async def api_resolve_unmatched(request: Request, db: Session = Depends(get_db)):
    body         = await request.json()
    line_id      = body.get("line_id")
    product      = (body.get("product") or "").strip()
    qty          = body.get("qty")
    unit         = (body.get("unit") or "kg").strip()
    order_id     = body.get("order_id")
    confirmed_by = (body.get("confirmed_by") or "plant_manager").strip()

    line = db.get(OcrUnmatchedLine, line_id)
    if not line:
        return JSONResponse(status_code=404, content={"error": "Line not found"})

    if product and qty and order_id:
        try:
            quantity = Decimal(str(qty))
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid quantity"})

        now = datetime.now(timezone.utc)

        # Order-time unclear items had no ordered_quantity at all (it was
        # never parsed) -- the resolved qty becomes both ordered & actual.
        # OCR-unmatched lines (from a photo) already have an ordered qty
        # seeded on the order; only set actual_quantity in that case.
        existing = None
        if line.reason == ORDER_TIME_UNCLEAR_REASON:
            existing = db.scalars(
                select(OrderItemActual).where(
                    OrderItemActual.order_id == order_id,
                    OrderItemActual.product == product,
                )
            ).first()

        if existing:
            existing.actual_quantity = quantity
            existing.actual_unit     = unit
            existing.confidence      = "auto"
            existing.confirmed_by    = confirmed_by
            existing.confirmed_at    = now
        else:
            db.add(OrderItemActual(
                order_id=order_id,
                product=product,
                ordered_quantity=quantity,
                ordered_unit=unit,
                actual_quantity=quantity,
                actual_unit=unit,
                capture_source="manual_resolve",
                confidence="auto",
                confirmed_by=confirmed_by,
                confirmed_at=now,
            ))

        line.resolved          = True
        line.resolved_product  = product
        line.resolved_quantity = quantity
        line.resolved_unit     = unit
        line.resolved_by       = confirmed_by
        line.resolved_at       = now
        db.commit()

    return {"ok": True}


# ---------------------------------------------------------------------------
# generate-invoice (unchanged)
# ---------------------------------------------------------------------------

@router.post("/billing/api/generate-invoice")
async def api_generate_invoice(request: Request, db: Session = Depends(get_db)):
    body              = await request.json()
    order_id          = body.get("order_id")
    customer_phone    = (body.get("customer_phone") or "").strip()
    business_date_str = body.get("business_date") or date.today().isoformat()

    try:
        business_date = date.fromisoformat(business_date_str)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid date"})

    if not customer_phone:
        row = db.execute(
            text("SELECT customer_phone FROM orders WHERE id = :oid"),
            {"oid": order_id},
        ).fetchone()
        if row:
            customer_phone = row[0] or ""

    try:
        invoice = generate_invoice(
            db=db,
            order_id=order_id,
            customer_phone=customer_phone,
            business_date=business_date,
        )
        return {
            "ok":             True,
            "invoice_number": invoice.invoice_number,
            "invoice_id":     invoice.id,
            "total":          float(invoice.total),
        }
    except InvoiceAlreadyExistsError as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except InvoiceHoldError as e:
        return JSONResponse(status_code=422, content={"error": str(e)})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        logger.exception("Unexpected invoice generation error")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------------------------------------------------------------------
# invoices/all (unchanged)
# ---------------------------------------------------------------------------

@router.get("/billing/api/invoices/all")
def api_invoices_all(db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT
                i.invoice_number,
                i.business_date,
                i.customer_phone,
                i.total,
                o.customer_name AS hotel_name
            FROM invoices i
            LEFT JOIN orders o ON o.id = i.order_id
            ORDER BY i.business_date DESC, i.invoice_number DESC
        """)
    ).fetchall()

    return {
        "invoices": [
            {
                "invoice_number": r[0],
                "business_date":  str(r[1])[:10],
                "customer_phone": r[2],
                "total":          float(r[3]),
                "hotel_name":     r[4],
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# PDF download (unchanged)
# ---------------------------------------------------------------------------

@router.get("/billing/api/invoices/{invoice_number}/pdf")
def api_invoice_pdf_by_number(invoice_number: str, db: Session = Depends(get_db)):
    invoice = db.scalar(select(Invoice).where(Invoice.invoice_number == invoice_number))
    if not invoice:
        return JSONResponse(
            status_code=404,
            content={"error": f"Invoice {invoice_number!r} not found."},
        )

    row = db.execute(
        text("SELECT customer_name FROM orders WHERE id = :oid"),
        {"oid": invoice.order_id},
    ).first()
    hotel_name = row[0] if row else invoice.customer_phone

    safe_name = (hotel_name or "").strip().replace(" ", "_").replace("/", "-")
    pdf_path  = Path("invoices") / f"{safe_name}_{invoice.invoice_number}.pdf"

    if not pdf_path.exists():
        try:
            generate_invoice_pdf(invoice, hotel_name)
        except Exception as e:
            logger.exception("PDF regeneration failed for invoice %s", invoice_number)
            return JSONResponse(status_code=500, content={"error": f"PDF generation failed: {e}"})

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=f"{safe_name}_{invoice.invoice_number}.pdf",
    )


# ---------------------------------------------------------------------------
# Bulk ZIP (unchanged)
# ---------------------------------------------------------------------------

@router.get("/billing/api/invoices/pdf/bulk")
def api_invoices_pdf_bulk(
    business_date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if business_date:
        try:
            target_date = date.fromisoformat(business_date)
        except ValueError:
            return JSONResponse(status_code=400, content={"error": f"Invalid date: {business_date!r}"})
    else:
        target_date = _today()

    invoices = db.scalars(
        select(Invoice)
        .where(Invoice.business_date == target_date)
        .order_by(Invoice.invoice_number)
    ).all()

    if not invoices:
        return JSONResponse(
            status_code=404,
            content={"error": f"No invoices found for {target_date.isoformat()}."},
        )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for invoice in invoices:
            row = db.execute(
                text("SELECT customer_name FROM orders WHERE id = :oid"),
                {"oid": invoice.order_id},
            ).first()
            hotel_name = row[0] if row else invoice.customer_phone

            safe_name = (hotel_name or "").strip().replace(" ", "_").replace("/", "-")
            pdf_path  = Path("invoices") / f"{safe_name}_{invoice.invoice_number}.pdf"

            if not pdf_path.exists():
                try:
                    generate_invoice_pdf(invoice, hotel_name)
                except Exception:
                    logger.exception(
                        "Skipping invoice %s in bulk zip -- PDF generation failed",
                        invoice.invoice_number,
                    )
                    continue

            if pdf_path.exists():
                zf.write(pdf_path, arcname=f"{safe_name}_{invoice.invoice_number}.pdf")

    zip_buffer.seek(0)
    filename = f"invoices-{target_date.isoformat()}.zip"
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )