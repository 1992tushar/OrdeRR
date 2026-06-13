"""
app/services/billing_service.py

Billing service for OrdeRR — price lookup, invoice creation, bulk generation,
migration guard, and auto-invoice hook.

All DB writes roll back on exception.
try_auto_invoice() NEVER raises — it is safe to call from order_service.
"""

import json
import logging
import os
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.invoice import CustomerProductPrice, DefaultProductPrice, Invoice
from app.models.order import Order

logger = logging.getLogger(__name__)

INVOICE_DIR = os.getenv("INVOICE_DIR", "./invoices")


# ── 1. Migration guard ─────────────────────────────────────────────────────────

def ensure_billing_schema(engine) -> None:
    """
    Add orders.invoice_id column if it does not already exist.
    Safe to call multiple times — idempotent.
    """
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='orders' AND column_name='invoice_id'"
        ))
        if not result.fetchone():
            conn.execute(text(
                "ALTER TABLE orders ADD COLUMN invoice_id INTEGER REFERENCES invoices(id)"
            ))
            conn.commit()
            logger.info("billing_service: added orders.invoice_id column")
        else:
            logger.debug("billing_service: orders.invoice_id already exists — skipping migration")


# ── 2. Price lookup ────────────────────────────────────────────────────────────

def get_price(customer_phone: str, product_name: str, db: Session) -> Optional[float]:
    """
    Exact logic from Section 10:
    1. Customer-specific price
    2. Default price fallback
    3. None → invoice blocked
    """
    # 1. Customer-specific price
    price = db.query(CustomerProductPrice).filter_by(
        customer_phone=customer_phone,
        product_name=product_name,
    ).first()
    if price:
        return float(price.price_per_unit)

    # 2. Default price fallback
    default = db.query(DefaultProductPrice).filter_by(
        product_name=product_name,
    ).first()
    if default:
        return float(default.price_per_unit)

    # 3. None → invoice blocked, user must set price
    return None


# ── 3. Invoice number sequence ─────────────────────────────────────────────────

def get_next_invoice_number(db: Session) -> tuple[int, str]:
    """
    Exact logic from Section 11.
    Returns (integer, formatted_string) e.g. (1, "INV1").
    Respects INVOICE_START env var on first invoice.
    """
    prefix = os.getenv("INVOICE_PREFIX", "INV")
    start  = int(os.getenv("INVOICE_START", "1"))
    last   = db.query(func.max(Invoice.invoice_number)).scalar() or (start - 1)
    next_num = last + 1
    return next_num, f"{prefix}{next_num}"


# ── Helper: safe JSONB list ────────────────────────────────────────────────────

def _safe_list(value) -> list:
    """Safely decode a JSONB/string field to a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


# ── 4. Create a single invoice ─────────────────────────────────────────────────

def create_invoice(
    order_id: int,
    invoice_date: str,
    line_items_override: Optional[list],
    db: Session,
) -> dict:
    """
    Generate an invoice for one order.

    Parameters
    ----------
    order_id            : DB id of the Order to bill.
    invoice_date        : ISO date string "YYYY-MM-DD".
    line_items_override : If provided, use these line items instead of looked-up prices.
                          Each item must have keys: product, quantity, unit, unit_price.
    db                  : SQLAlchemy session.

    Returns
    -------
    dict with keys: invoice_id, invoice_number, pdf_path

    Raises
    ------
    ValueError  : Order not found, or price missing with no override.
    Exception   : PDF write failure (DB record NOT saved in this case).
    """
    # Lazy import — only loaded when billing flag is on
    from app.services.invoice_generator import generate_invoice_pdf

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError(f"Order {order_id} not found")

    parsed_items = _safe_list(order.parsed_items)

    # ── Build line items ──────────────────────────────────────────────────────
    if line_items_override is not None:
        line_items = line_items_override
    else:
        line_items = []
        missing = []
        for item in parsed_items:
            product  = item.get("product", "")
            quantity = item.get("quantity", 0)
            unit     = item.get("unit", "KGS")
            price    = get_price(order.customer_phone, product, db)
            if price is None:
                missing.append(product)
            else:
                line_items.append({
                    "product":    product,
                    "quantity":   quantity,
                    "unit":       unit,
                    "unit_price": price,
                    "net_amount": round(quantity * price, 3),
                })
        if missing:
            raise ValueError(
                f"Price missing for product(s): {', '.join(missing)}. "
                "Set prices before generating invoice."
            )

    # ── Totals ────────────────────────────────────────────────────────────────
    subtotal          = round(sum(li["net_amount"] for li in line_items), 3)
    additional_charge = 0.0
    round_off         = 0.0
    total_amount      = round(subtotal + additional_charge + round_off, 3)
    due_amount        = total_amount

    # ── Invoice number ────────────────────────────────────────────────────────
    invoice_number_int, invoice_number_str = get_next_invoice_number(db)

    # ── PDF filename ──────────────────────────────────────────────────────────
    safe_phone = (order.customer_phone or "").replace("+", "")
    date_tag   = invoice_date.replace("-", "")
    pdf_filename = f"{invoice_number_str}_{safe_phone}_{date_tag}.pdf"
    pdf_path     = os.path.join(INVOICE_DIR, pdf_filename)

    # ── Generate PDF FIRST — if this fails we must NOT commit to DB ───────────
    try:
        os.makedirs(INVOICE_DIR, exist_ok=True)
        generate_invoice_pdf(
            pdf_path=pdf_path,
            invoice_number=invoice_number_str,
            invoice_date=invoice_date,
            customer_name=order.customer_name or "",
            customer_phone=order.customer_phone or "",
            line_items=line_items,
            subtotal=subtotal,
            additional_charge=additional_charge,
            round_off=round_off,
            total_amount=total_amount,
        )
    except Exception as pdf_err:
        logger.error(
            "billing_service: PDF generation failed for order %s: %s",
            order_id, pdf_err, exc_info=True,
        )
        raise  # propagate — DB record NOT saved (per Section 13)

    # ── Persist invoice record ────────────────────────────────────────────────
    try:
        invoice = Invoice(
            invoice_number    = invoice_number_int,
            order_id          = order_id,
            customer_phone    = order.customer_phone,
            customer_name     = order.customer_name,
            invoice_date      = date.fromisoformat(invoice_date),
            line_items        = line_items,
            subtotal          = subtotal,
            additional_charge = additional_charge,
            round_off         = round_off,
            total_amount      = total_amount,
            due_amount        = due_amount,
            pdf_path          = pdf_filename,   # relative path under INVOICE_DIR
            status            = "generated",
            generated_by      = "manual",
        )
        db.add(invoice)
        db.flush()  # get invoice.id without committing yet

        # Link order → invoice
        order.invoice_id = invoice.id
        db.commit()
        db.refresh(invoice)

        logger.info(
            "billing_service: invoice %s created for order %s (id=%s)",
            invoice_number_str, order_id, invoice.id,
        )
        return {
            "invoice_id":     invoice.id,
            "invoice_number": invoice_number_str,
            "pdf_path":       pdf_path,
        }

    except Exception as db_err:
        db.rollback()
        logger.error(
            "billing_service: DB write failed for invoice (order %s): %s",
            order_id, db_err, exc_info=True,
        )
        raise


# ── 5. Bulk invoice generation ─────────────────────────────────────────────────

def create_bulk_invoices(date_str: str, db: Session) -> dict:
    """
    Find all orders for date_str without an invoice, generate each.
    Returns {"success": N, "failed": M, "errors": [...]}.
    Per Section 13: generate as many as possible; report counts.
    """
    orders = (
        db.query(Order)
        .filter(
            Order.business_date == date_str,
            Order.is_cancelled.is_(False),
            Order.invoice_id.is_(None),
        )
        .all()
    )

    success = 0
    failed  = 0
    errors  = []

    invoice_date = date_str  # use the order's business date as invoice date

    for order in orders:
        try:
            create_invoice(
                order_id            = order.id,
                invoice_date        = invoice_date,
                line_items_override = None,
                db                  = db,
            )
            success += 1
        except Exception as e:
            failed += 1
            msg = f"Order {order.id} ({order.customer_name}): {e}"
            errors.append(msg)
            logger.warning("billing_service bulk: %s", msg)

    logger.info(
        "billing_service bulk: %s success=%d failed=%d for date %s",
        date_str, success, failed, date_str,
    )
    return {"success": success, "failed": failed, "errors": errors}


# ── 6. Auto-invoice hook ───────────────────────────────────────────────────────

def try_auto_invoice(db: Session, order: Order) -> Optional[dict]:
    """
    Called from order_service after order save (behind feature flags).
    NEVER raises — all exceptions are caught and logged.

    Returns result dict on success, None on skip or failure.
    """
    # Guard: only auto-invoice confirmed orders not yet billed
    if order.status != "confirmed":
        return None
    if getattr(order, "invoice_id", None) is not None:
        return None

    # Check all prices available before attempting
    parsed_items = _safe_list(order.parsed_items)
    for item in parsed_items:
        product = item.get("product", "")
        if get_price(order.customer_phone, product, db) is None:
            logger.info(
                "billing_service auto: skipping order %s — prices missing for '%s'",
                order.id, product,
            )
            return None

    # All prices present — attempt invoice creation
    try:
        invoice_date = (
            order.business_date
            if order.business_date
            else datetime.utcnow().date().isoformat()
        )
        result = create_invoice(
            order_id            = order.id,
            invoice_date        = invoice_date,
            line_items_override = None,
            db                  = db,
        )
        # Patch generated_by to 'auto' after commit
        try:
            inv = db.query(Invoice).filter(Invoice.id == result["invoice_id"]).first()
            if inv:
                inv.generated_by = "auto"
                db.commit()
        except Exception:
            pass  # non-critical
        logger.info(
            "billing_service auto: invoice %s generated for order %s",
            result["invoice_number"], order.id,
        )
        return result

    except Exception as e:
        logger.error(
            "billing_service auto: invoice generation failed for order %s: %s",
            order.id, e, exc_info=True,
        )
        return None


# ── 7. Billing summary ─────────────────────────────────────────────────────────

def get_billing_summary(date_str: str, db: Session) -> dict:
    """
    Returns {"total_orders": N, "billed": N, "unbilled": N, "revenue": N}.
    """
    orders = (
        db.query(Order)
        .filter(
            Order.business_date == date_str,
            Order.is_cancelled.is_(False),
        )
        .all()
    )

    total   = len(orders)
    billed  = sum(1 for o in orders if getattr(o, "invoice_id", None) is not None)
    unbilled = total - billed

    # Revenue = sum of total_amount across invoices for this date
    revenue_row = (
        db.query(func.sum(Invoice.total_amount))
        .filter(Invoice.invoice_date == date.fromisoformat(date_str))
        .scalar()
    )
    revenue = float(revenue_row) if revenue_row else 0.0

    return {
        "total_orders": total,
        "billed":       billed,
        "unbilled":     unbilled,
        "revenue":      revenue,
    }


# ── 8. Orders with billing status ─────────────────────────────────────────────

def get_orders_with_billing_status(date_str: str, db: Session) -> list:
    """
    Returns list of order dicts with added "billing_status" field:
      "billed"         — has invoice_id
      "prices_missing" — one or more products have no price
      "unbilled"       — all prices set but not yet billed
    """
    orders = (
        db.query(Order)
        .filter(
            Order.business_date == date_str,
            Order.is_cancelled.is_(False),
        )
        .all()
    )

    result = []
    for order in orders:
        if getattr(order, "invoice_id", None) is not None:
            billing_status = "billed"
        else:
            parsed_items = _safe_list(order.parsed_items)
            any_missing = any(
                get_price(order.customer_phone, item.get("product", ""), db) is None
                for item in parsed_items
            )
            billing_status = "prices_missing" if any_missing else "unbilled"

        result.append({
            "id":             order.id,
            "customer_name":  order.customer_name,
            "customer_phone": order.customer_phone,
            "business_date":  order.business_date,
            "status":         order.status,
            "invoice_id":     getattr(order, "invoice_id", None),
            "is_unclear":     order.is_unclear,
            "parsed_items":   _safe_list(order.parsed_items),
            "billing_status": billing_status,
        })

    return result