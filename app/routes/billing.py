"""
app/routes/billing.py

All /admin/billing/* endpoints.
Router is only registered in app/main.py when FLAG_BILLING_ENABLED=true.
Auth pattern mirrors existing admin routes (HTTP Basic via require_auth).
DB dependency mirrors existing routes (get_db).
"""

import io
import logging
import os
import zipfile
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.config.flags import is_enabled
from app.database import get_db
from app.models.invoice import (
    CustomerProductPrice,
    DefaultProductPrice,
    Invoice,
)
from app.services import billing_service

logger = logging.getLogger(__name__)

router = APIRouter()

INVOICE_DIR = os.getenv("INVOICE_DIR", "./invoices")


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class CustomerPriceUpsert(BaseModel):
    customer_phone: str
    product_name:   str
    price_per_unit: float
    uom:            str = "KGS"


class DefaultPriceUpsert(BaseModel):
    product_name:   str
    price_per_unit: float
    uom:            str = "KGS"


class GenerateInvoicePayload(BaseModel):
    order_id:             int
    invoice_date:         str                    # "YYYY-MM-DD"
    line_items_override:  Optional[list] = None


class GenerateBulkPayload(BaseModel):
    date: str                                    # "YYYY-MM-DD"


# ── helpers ────────────────────────────────────────────────────────────────────

def _invoice_dict(inv: Invoice) -> dict:
    return {
        "id":                inv.id,
        "invoice_number":    inv.invoice_number,
        "order_id":          inv.order_id,
        "customer_phone":    inv.customer_phone,
        "customer_name":     inv.customer_name,
        "invoice_date":      str(inv.invoice_date),
        "line_items":        inv.line_items,
        "subtotal":          float(inv.subtotal)          if inv.subtotal          is not None else None,
        "additional_charge": float(inv.additional_charge) if inv.additional_charge is not None else None,
        "round_off":         float(inv.round_off)         if inv.round_off         is not None else None,
        "total_amount":      float(inv.total_amount)      if inv.total_amount      is not None else None,
        "due_amount":        float(inv.due_amount)        if inv.due_amount        is not None else None,
        "pdf_path":          inv.pdf_path,
        "status":            inv.status,
        "generated_at":      inv.generated_at.isoformat() if inv.generated_at else None,
        "generated_by":      inv.generated_by,
    }


def _customer_price_dict(p: CustomerProductPrice) -> dict:
    return {
        "id":             p.id,
        "customer_phone": p.customer_phone,
        "product_name":   p.product_name,
        "price_per_unit": float(p.price_per_unit),
        "uom":            p.uom,
        "created_at":     p.created_at.isoformat() if p.created_at else None,
        "updated_at":     p.updated_at.isoformat() if p.updated_at else None,
    }


def _default_price_dict(p: DefaultProductPrice) -> dict:
    return {
        "id":             p.id,
        "product_name":   p.product_name,
        "price_per_unit": float(p.price_per_unit),
        "uom":            p.uom,
        "updated_at":     p.updated_at.isoformat() if p.updated_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6.1 — PRICING ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/prices/defaults")
def get_default_prices(
    db:       Session = Depends(get_db),
    username: str     = Depends(require_auth),
):
    """List all DefaultProductPrice rows."""
    rows = db.query(DefaultProductPrice).order_by(DefaultProductPrice.product_name).all()
    return [_default_price_dict(r) for r in rows]


@router.post("/prices/defaults")
def upsert_default_price(
    payload:  DefaultPriceUpsert,
    db:       Session = Depends(get_db),
    username: str     = Depends(require_auth),
):
    """Create or update a default price for a product."""
    existing = db.query(DefaultProductPrice).filter_by(
        product_name=payload.product_name,
    ).first()
    if existing:
        existing.price_per_unit = payload.price_per_unit
        existing.uom            = payload.uom
        db.commit()
        db.refresh(existing)
        return _default_price_dict(existing)
    row = DefaultProductPrice(
        product_name   = payload.product_name,
        price_per_unit = payload.price_per_unit,
        uom            = payload.uom,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _default_price_dict(row)


# NOTE: /prices/defaults must be declared BEFORE /prices/{customer_phone}
# so FastAPI does not treat "defaults" as a phone number path segment.

@router.get("/prices/{customer_phone}")
def get_customer_prices(
    customer_phone: str,
    db:             Session = Depends(get_db),
    username:       str     = Depends(require_auth),
):
    """List all CustomerProductPrice rows for a given phone number."""
    rows = (
        db.query(CustomerProductPrice)
        .filter_by(customer_phone=customer_phone)
        .order_by(CustomerProductPrice.product_name)
        .all()
    )
    return [_customer_price_dict(r) for r in rows]


@router.post("/prices")
def upsert_customer_price(
    payload:  CustomerPriceUpsert,
    db:       Session = Depends(get_db),
    username: str     = Depends(require_auth),
):
    """Create or update a customer-specific product price."""
    existing = db.query(CustomerProductPrice).filter_by(
        customer_phone=payload.customer_phone,
        product_name=payload.product_name,
    ).first()
    if existing:
        existing.price_per_unit = payload.price_per_unit
        existing.uom            = payload.uom
        db.commit()
        db.refresh(existing)
        return _customer_price_dict(existing)
    row = CustomerProductPrice(
        customer_phone = payload.customer_phone,
        product_name   = payload.product_name,
        price_per_unit = payload.price_per_unit,
        uom            = payload.uom,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _customer_price_dict(row)


@router.delete("/prices/{price_id}")
def delete_customer_price(
    price_id: int,
    db:       Session = Depends(get_db),
    username: str     = Depends(require_auth),
):
    """Delete a CustomerProductPrice row by id. 404 if not found."""
    row = db.query(CustomerProductPrice).filter(CustomerProductPrice.id == price_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Price id={price_id} not found")
    db.delete(row)
    db.commit()
    return {"deleted": True, "id": price_id}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6.2 — INVOICE ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

# NOTE: fixed-path routes (/invoices/generate, /invoices/generate-bulk,
# /invoices/download-all) must be declared BEFORE parameterised routes
# (/invoices/{invoice_id}) so FastAPI matches them correctly.

@router.get("/invoices")
def list_invoices(
    date:           Optional[str] = Query(None, description="YYYY-MM-DD"),
    customer_phone: Optional[str] = Query(None),
    status:         Optional[str] = Query(None),
    db:             Session       = Depends(get_db),
    username:       str           = Depends(require_auth),
):
    """List invoices, optionally filtered by date, customer_phone, status."""
    q = db.query(Invoice)
    if date:
        try:
            q = q.filter(Invoice.invoice_date == date)
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid date format; use YYYY-MM-DD")
    if customer_phone:
        q = q.filter(Invoice.customer_phone == customer_phone)
    if status:
        q = q.filter(Invoice.status == status)
    rows = q.order_by(Invoice.invoice_number.desc()).all()
    return [_invoice_dict(r) for r in rows]


@router.post("/invoices/generate")
def generate_invoice(
    payload:  GenerateInvoicePayload,
    db:       Session = Depends(get_db),
    username: str     = Depends(require_auth),
):
    """Generate an invoice for a single order."""
    try:
        # In the generate endpoint, BEFORE calling billing_service.create_invoice(...)
        # Add this block:
        from app.models.invoice import Invoice as InvoiceModel

        existing = db.query(InvoiceModel).filter(
            InvoiceModel.order_id == payload.order_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Order {payload.order_id} is already billed (invoice {existing.invoice_number})"
            )
        result = billing_service.create_invoice(
            order_id            = payload.order_id,
            invoice_date        = payload.invoice_date,
            line_items_override = payload.line_items_override,
            db                  = db,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise  # let 400/409 pass through unchanged
    except Exception as e:
        logger.error(f"billing routes: generate_invoice error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Invoice generation failed: {e}")

@router.post("/invoices/generate-bulk")
def generate_bulk(
    payload:  GenerateBulkPayload,
    db:       Session = Depends(get_db),
    username: str     = Depends(require_auth),
):
    """
    Generate invoices for all unbilled orders on a date.
    Requires FLAG_BILLING_BULK_GENERATE=true in addition to FLAG_BILLING_ENABLED=true.
    """
    if not is_enabled("FLAG_BILLING_BULK_GENERATE"):
        raise HTTPException(
            status_code=404,
            detail="Bulk invoice generation is not enabled (FLAG_BILLING_BULK_GENERATE=false)",
        )
    result = billing_service.create_bulk_invoices(date_str=payload.date, db=db)
    return result


@router.get("/invoices/download-all")
def download_all_pdfs(
    start_date: str     = Query(..., description="YYYY-MM-DD"),
    end_date:   str     = Query(..., description="YYYY-MM-DD"),
    db:         Session = Depends(get_db),
    username:   str     = Depends(require_auth),
):
    """
    Return a ZIP archive of all invoice PDFs in the given date range.
    """
    try:
        start = date.fromisoformat(start_date)
        end   = date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format; use YYYY-MM-DD")

    invoices = (
        db.query(Invoice)
        .filter(
            Invoice.invoice_date >= start,
            Invoice.invoice_date <= end,
            Invoice.status != "voided",
            Invoice.pdf_path.isnot(None),
        )
        .order_by(Invoice.invoice_number)
        .all()
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for inv in invoices:
            full_path = os.path.join(INVOICE_DIR, inv.pdf_path)
            if os.path.isfile(full_path):
                zf.write(full_path, arcname=os.path.basename(full_path))
            else:
                logger.warning("billing routes download-all: PDF not found on disk: %s", full_path)

    buf.seek(0)
    zip_filename = f"invoices_{start_date}_to_{end_date}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


@router.get("/invoices/{invoice_id}/pdf")
def download_invoice_pdf(
    invoice_id: int,
    db:         Session = Depends(get_db),
    username:   str     = Depends(require_auth),
):
    """Stream the PDF for an invoice. 404 if record or file is missing."""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail=f"Invoice id={invoice_id} not found")
    if not inv.pdf_path:
        raise HTTPException(status_code=404, detail="No PDF path recorded for this invoice")

    full_path = os.path.join(INVOICE_DIR, inv.pdf_path)
    if not os.path.isfile(full_path):
        raise HTTPException(
            status_code=404,
            detail=f"PDF file not found on disk: {inv.pdf_path}",
        )

    # Mark as downloaded (best-effort, non-fatal)
    try:
        if inv.status == "generated":
            inv.status = "downloaded"
            db.commit()
    except Exception:
        db.rollback()

    return FileResponse(
        path=full_path,
        media_type="application/pdf",
        filename=os.path.basename(full_path),
    )


@router.get("/invoices/{invoice_id}")
def get_invoice(
    invoice_id: int,
    db:         Session = Depends(get_db),
    username:   str     = Depends(require_auth),
):
    """Get a single invoice record by id."""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail=f"Invoice id={invoice_id} not found")
    return _invoice_dict(inv)


@router.delete("/invoices/{invoice_id}")
def void_invoice(
    invoice_id: int,
    db:         Session = Depends(get_db),
    username:   str     = Depends(require_auth),
):
    """
    Void an invoice (soft delete).
    Sets status='voided'; PDF kept on disk; DB record retained.
    """
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail=f"Invoice id={invoice_id} not found")
    if inv.status == "voided":
        raise HTTPException(status_code=409, detail="Invoice is already voided")

    try:
        inv.status = "voided"
        # Unlink order → invoice so the order becomes unbilled again
        from app.models.order import Order
        order = db.query(Order).filter(Order.id == inv.order_id).first()
        if order and getattr(order, "invoice_id", None) == invoice_id:
            order.invoice_id = None
        #db.commit()
        db.flush()

        db.refresh(inv)
    except Exception as e:
        db.rollback()
        logger.error("billing routes: void_invoice error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to void invoice: {e}")

    return _invoice_dict(inv)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6.3 — DASHBOARD DATA ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/summary")
def billing_summary(
    query_date: Optional[str] = Query(None, alias="date", description="YYYY-MM-DD; defaults to today"),
    db:         Session       = Depends(get_db),
    username:   str           = Depends(require_auth),
):
    """Return billed/unbilled counts and revenue for a date."""
    date_str = query_date or date.today().isoformat()
    return billing_service.get_billing_summary(date_str=date_str, db=db)


@router.get("/orders/{date_str}")
def orders_with_billing(
    date_str: str,
    db:       Session = Depends(get_db),
    username: str     = Depends(require_auth),
):
    """Return orders for a date with billing_status field per order."""
    return billing_service.get_orders_with_billing_status(date_str=date_str, db=db)