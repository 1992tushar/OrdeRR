"""
routes/invoices.py

DEPRECATED SECTION (as of the orderr-core direct-integration refactor):
─────────────────────────────────────────────────────────────────────
  GET/POST /dashboard/actuals/upload
  GET/POST /dashboard/actuals/{order_id}
  GET      /dashboard/actuals/{order_id}/review
  POST     /dashboard/actuals/{order_id}/review/{actual_id}/confirm
  POST     /dashboard/actuals/{order_id}/review/unmatched/{line_id}/resolve

  These routes used to be how orders got created and actuals got captured:
  the /upload endpoint matched a photo-OCR'd hotel name against `customer_name`
  + today's date and would CREATE a new order if nothing matched
  (`_find_or_create_order`). This is the exact duplicate-order risk that
  caused us to move order creation upstream.

  Orders are now always sourced directly from orderr-core (it writes
  `orders` rows the moment a customer places an order, with `parsed_items`
  /`unclear_items` already structured). The new home for all of this is
  `app/routes/billing.py` + `/billing` (the SPA dashboard):
    - today's orders + delivered-quantity confirmation -> GET /billing
    - photo upload to confirm delivery                -> POST /billing/api/upload
    - resolving unclear/unmatched lines                -> POST /billing/api/resolve-unmatched
    - confirming a needs-review quantity                -> POST /billing/api/fix-item

  The routes below are kept registered (so old bookmarks/links don't 404)
  but now just redirect to /billing, or block the old POST upload path
  outright so it can never create a duplicate order again.

STILL ACTIVE — unrelated to the duplication problem, left as-is:
─────────────────────────────────────────────────────────────────
  GET  /dashboard/invoices                       — list all invoices
  POST /dashboard/invoices/generate              — generate from order_id (form submit)
  GET  /dashboard/invoices/{invoice_id}          — detail + line items
  GET  /dashboard/invoices/{invoice_id}/pdf       — PDF download
  GET  /dashboard/invoices/overrides             — list all overrides
  POST /dashboard/invoices/overrides             — create override
  POST /dashboard/invoices/overrides/{id}/delete — soft-delete (sets effective_to=today)
"""
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orderr_core.database import get_db
from orderr_core.models.invoice import Invoice, InvoiceItem
from orderr_core.models.rate_override import CustomerRateOverride
from orderr_core.services.invoice_generator import (
    generate_invoice,
    InvoiceHoldError,
    InvoiceAlreadyExistsError,
)
from orderr_core.services.invoice_pdf import generate_invoice_pdf

logger = logging.getLogger(__name__)

router = APIRouter()
from orderr_core.templating import make_templates
templates = make_templates()

_DEPRECATION_NOTICE = (
    "This page has moved. Orders now appear automatically on the Billing "
    "dashboard as soon as a customer places them — no manual upload needed."
)


# ═══════════════════════════════════════════════════════════════════════════
# DEPRECATED — actuals photo upload (used to create duplicate orders)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/dashboard/actuals/upload", response_class=HTMLResponse)
def actuals_upload_page(request: Request):
    logger.info("Deprecated route hit: GET /dashboard/actuals/upload -> redirecting to /billing")
    return RedirectResponse(url="/billing", status_code=308)


@router.post("/dashboard/actuals/upload", response_class=HTMLResponse)
async def actuals_photo_upload(request: Request):
    """
    Deprecated. This used to call _find_or_create_order(), which could
    create a duplicate order if the photo's hotel name didn't fuzzy-match
    an existing one. Order creation now happens exclusively in orderr-core.
    Blocked outright rather than redirected, since this was a POST that
    used to mutate the DB -- a 308 redirect could be silently re-submitted
    by some HTTP clients as a POST to /billing, which has a different
    multipart contract.
    """
    logger.warning(
        "Deprecated route hit: POST /dashboard/actuals/upload (blocked — "
        "use POST /billing/api/upload instead)"
    )
    return templates.TemplateResponse(request, "dashboard_actuals_upload.html", {
        "error": _DEPRECATION_NOTICE,
        "hotel_summaries": None,
        "today": None,
    }, status_code=410)


# ═══════════════════════════════════════════════════════════════════════════
# DEPRECATED — manual actuals entry (superseded by /billing/api/fix-item
# and /billing/api/resolve-unmatched, which operate on orderr-core orders)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/dashboard/actuals/{order_id}", response_class=HTMLResponse)
def actuals_form(request: Request, order_id: int, db: Session = Depends(get_db)):
    logger.info("Deprecated route hit: GET /dashboard/actuals/%d -> redirecting to /billing", order_id)
    return RedirectResponse(url="/billing", status_code=308)


@router.post("/dashboard/actuals/{order_id}", response_class=HTMLResponse)
async def actuals_save(request: Request, order_id: int):
    logger.warning(
        "Deprecated route hit: POST /dashboard/actuals/%d (blocked — "
        "use the Billing dashboard instead)", order_id,
    )
    return templates.TemplateResponse(request, "dashboard_actuals.html", {
        "order_id": order_id,
        "actuals": [],
        "saved": False,
        "error": _DEPRECATION_NOTICE,
        "ocr_summary": None,
    }, status_code=410)


# ═══════════════════════════════════════════════════════════════════════════
# DEPRECATED — review queue (superseded by the "Needs attention" section
# on the Billing dashboard, backed by /billing/api/today-results)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/dashboard/actuals/{order_id}/review", response_class=HTMLResponse)
def actuals_review(request: Request, order_id: int):
    logger.info("Deprecated route hit: GET /dashboard/actuals/%d/review -> redirecting to /billing", order_id)
    return RedirectResponse(url="/billing", status_code=308)


@router.post(
    "/dashboard/actuals/{order_id}/review/{actual_id}/confirm",
    response_class=HTMLResponse,
)
async def actuals_review_confirm(request: Request, order_id: int, actual_id: int):
    logger.warning(
        "Deprecated route hit: POST /dashboard/actuals/%d/review/%d/confirm "
        "(blocked — use POST /billing/api/fix-item instead)", order_id, actual_id,
    )
    return templates.TemplateResponse(request, "dashboard_actuals_review.html", {
        "order_id": order_id,
        "needs_review": [],
        "unmatched": [],
        "error": _DEPRECATION_NOTICE,
        "success": None,
    }, status_code=410)


@router.post(
    "/dashboard/actuals/{order_id}/review/unmatched/{line_id}/resolve",
    response_class=HTMLResponse,
)
async def actuals_review_resolve_unmatched(request: Request, order_id: int, line_id: int):
    logger.warning(
        "Deprecated route hit: POST /dashboard/actuals/%d/review/unmatched/%d/resolve "
        "(blocked — use POST /billing/api/resolve-unmatched instead)", order_id, line_id,
    )
    return templates.TemplateResponse(request, "dashboard_actuals_review.html", {
        "order_id": order_id,
        "needs_review": [],
        "unmatched": [],
        "error": _DEPRECATION_NOTICE,
        "success": None,
    }, status_code=410)


# ═══════════════════════════════════════════════════════════════════════════
# INVOICES — list + generate + detail (unchanged — not part of the
# order-duplication problem, left fully active)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/dashboard/invoices", response_class=HTMLResponse)
def invoices_list(request: Request, db: Session = Depends(get_db)):
    invoices = db.scalars(
        select(Invoice).order_by(Invoice.business_date.desc(), Invoice.invoice_number.desc())
    ).all()
    return templates.TemplateResponse(request, "dashboard_invoices.html", {
        "invoices": invoices,
        "error": None,
        "success": None,
    })


@router.post("/dashboard/invoices/generate", response_class=HTMLResponse)
async def invoices_generate(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    order_id_raw      = form.get("order_id", "").strip()
    customer_phone    = form.get("customer_phone", "").strip()
    business_date_raw = form.get("business_date", "").strip()

    invoices = db.scalars(
        select(Invoice).order_by(Invoice.business_date.desc(), Invoice.invoice_number.desc())
    ).all()

    def _error(msg: str):
        return templates.TemplateResponse(request, "dashboard_invoices.html", {
            "invoices": invoices,
            "error": msg,
            "success": None,
        })

    if not order_id_raw or not customer_phone or not business_date_raw:
        return _error("Order ID, customer phone, and business date are all required.")

    try:
        order_id = int(order_id_raw)
    except ValueError:
        return _error(f"Order ID must be a number, got: {order_id_raw!r}")

    try:
        business_date = date.fromisoformat(business_date_raw)
    except ValueError:
        return _error(f"Invalid date format: {business_date_raw!r}. Use YYYY-MM-DD.")

    try:
        invoice = generate_invoice(
            db=db,
            order_id=order_id,
            customer_phone=customer_phone,
            business_date=business_date,
        )
    except InvoiceAlreadyExistsError as e:
        return _error(str(e))
    except InvoiceHoldError as e:
        return _error(f"🔴 Billing held: {e}")
    except ValueError as e:
        return _error(str(e))
    except Exception as e:
        return _error(f"Unexpected error: {e}")

    invoices = db.scalars(
        select(Invoice).order_by(Invoice.business_date.desc(), Invoice.invoice_number.desc())
    ).all()
    return templates.TemplateResponse(request, "dashboard_invoices.html", {
        "invoices": invoices,
        "error": None,
        "success": f"✅ Invoice {invoice.invoice_number} generated (draft). Total: ₹{invoice.total:.2f}",
    })


@router.get("/dashboard/invoices/overrides", response_class=HTMLResponse)
def overrides_list(request: Request, db: Session = Depends(get_db)):
    overrides = db.scalars(
        select(CustomerRateOverride).order_by(
            CustomerRateOverride.customer_phone,
            CustomerRateOverride.product,
            CustomerRateOverride.effective_from.desc(),
        )
    ).all()
    return templates.TemplateResponse(request, "dashboard_overrides.html", {
        "overrides": overrides,
        "error": None,
        "success": None,
    })


@router.post("/dashboard/invoices/overrides", response_class=HTMLResponse)
async def overrides_create(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    customer_phone     = form.get("customer_phone", "").strip()
    product            = form.get("product", "").strip()
    rate_raw           = form.get("rate_per_unit", "").strip()
    unit               = form.get("unit", "").strip()
    effective_from_raw = form.get("effective_from", "").strip()
    effective_to_raw   = form.get("effective_to", "").strip()

    overrides = db.scalars(
        select(CustomerRateOverride).order_by(
            CustomerRateOverride.customer_phone,
            CustomerRateOverride.effective_from.desc(),
        )
    ).all()

    def _error(msg: str):
        return templates.TemplateResponse(request, "dashboard_overrides.html", {
            "overrides": overrides,
            "error": msg,
            "success": None,
        })

    if not all([customer_phone, product, rate_raw, unit, effective_from_raw]):
        return _error("All fields except Effective To are required.")

    try:
        rate = Decimal(rate_raw)
        if rate <= 0:
            raise ValueError
    except Exception:
        return _error(f"Rate must be a positive number, got: {rate_raw!r}")

    try:
        effective_from = date.fromisoformat(effective_from_raw)
    except ValueError:
        return _error(f"Invalid effective_from date: {effective_from_raw!r}")

    effective_to: Optional[date] = None
    if effective_to_raw:
        try:
            effective_to = date.fromisoformat(effective_to_raw)
            if effective_to < effective_from:
                return _error("Effective To must be on or after Effective From.")
        except ValueError:
            return _error(f"Invalid effective_to date: {effective_to_raw!r}")

    db.add(CustomerRateOverride(
        customer_phone=customer_phone,
        product=product,
        rate_per_unit=rate,
        unit=unit,
        effective_from=effective_from,
        effective_to=effective_to,
    ))
    db.commit()

    overrides = db.scalars(
        select(CustomerRateOverride).order_by(
            CustomerRateOverride.customer_phone,
            CustomerRateOverride.effective_from.desc(),
        )
    ).all()
    return templates.TemplateResponse(request, "dashboard_overrides.html", {
        "overrides": overrides,
        "error": None,
        "success": f"✅ Override created for {customer_phone} / {product} from {effective_from}.",
    })


@router.post(
    "/dashboard/invoices/overrides/{override_id}/delete",
    response_class=HTMLResponse,
)
def overrides_deactivate(
    request: Request,
    override_id: int,
    db: Session = Depends(get_db),
):
    override = db.get(CustomerRateOverride, override_id)
    if override and override.effective_to is None:
        override.effective_to = date.today()
        db.commit()
    return RedirectResponse("/dashboard/invoices/overrides", status_code=303)


@router.get("/dashboard/invoices/{invoice_id}/pdf")
def invoice_pdf_download(invoice_id: int, db: Session = Depends(get_db)):
    """
    Return the PDF for a given invoice, generating it on-the-fly if missing.
    """
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        return HTMLResponse("<h2>Invoice not found.</h2>", status_code=404)

    pdf_path = Path("invoices") / f"{invoice.invoice_number}.pdf"

    if not pdf_path.exists():
        hotel_row = db.execute(
            text("SELECT customer_name FROM orders WHERE id = :oid"),
            {"oid": invoice.order_id},
        ).first()
        hotel_name = hotel_row[0] if hotel_row else invoice.customer_phone
        generate_invoice_pdf(invoice, hotel_name)

    return FileResponse(
        path=str(pdf_path.resolve()),
        media_type="application/pdf",
        filename=f"{invoice.invoice_number}.pdf",
    )


@router.get("/dashboard/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        return HTMLResponse("<h2>Invoice not found.</h2>", status_code=404)

    items = db.scalars(
        select(InvoiceItem).where(InvoiceItem.invoice_id == invoice_id)
    ).all()

    return templates.TemplateResponse(request, "dashboard_invoice_detail.html", {
        "invoice": invoice,
        "items": items,
    })