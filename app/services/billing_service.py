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
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.invoice import CustomerProductPrice, DefaultProductPrice, Invoice, ProductItemCode
from app.models.order import Order

logger = logging.getLogger(__name__)

INVOICE_DIR = os.getenv("INVOICE_DIR", "./invoices")
IST = timezone(timedelta(hours=5, minutes=30))


# ── 1. Migration guard ─────────────────────────────────────────────────────────

def ensure_billing_schema(engine) -> None:
    """
    Add orders.invoice_id column if it does not already exist.
    Safe to call multiple times — idempotent.
    """
    with engine.connect() as conn:
        dialect = engine.dialect.name

        if dialect == "sqlite":
            result = conn.execute(text("PRAGMA table_info('orders')"))
            columns = [row[1] for row in result]
            has_invoice_id = "invoice_id" in columns
        else:
            result = conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='orders' AND column_name='invoice_id'"
            ))
            has_invoice_id = result.fetchone() is not None

        if not has_invoice_id:
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
    1. Customer-specific price
    2. Default price fallback
    3. None → invoice blocked
    """
    price = db.query(CustomerProductPrice).filter_by(
        customer_phone=customer_phone,
        product_name=product_name,
    ).first()
    if price:
        return float(price.price_per_unit)

    default = db.query(DefaultProductPrice).filter_by(
        product_name=product_name,
    ).first()
    if default:
        return float(default.price_per_unit)

    return None


# ── 3. Invoice number sequence ─────────────────────────────────────────────────

def get_next_invoice_number(db: Session) -> tuple[int, str]:
    """
    Returns (integer, formatted_string) e.g. (1, "INV1").
    Respects INVOICE_START env var on first invoice.
    """
    prefix = os.getenv("INVOICE_PREFIX", "INV")
    start  = int(os.getenv("INVOICE_START", "1"))
    last   = db.query(func.max(Invoice.invoice_number)).scalar() or (start - 1)
    next_num = last + 1
    return next_num, f"{prefix}{next_num}"


# ── Helper: item code lookup ───────────────────────────────────────────────────

def _get_item_code(product_name: str, db: Session) -> str:
    """Return item code for a product, or empty string if not configured."""
    row = db.query(ProductItemCode).filter_by(product_name=product_name).first()
    return row.item_code if row else ""


# ── Helper: build PDF-ready line item ─────────────────────────────────────────

def _build_line_item(sr: int, product: str, quantity: float, unit: str,
                     unit_price: float, item_code: str = "") -> dict:
    """
    Produce a dict with all keys expected by invoice_generator._draw_table():
      sr, description, item_code, qty, uom, unit_price,
      discount, discount2, rate, net_amount
    Also retains product/quantity/unit for billing_service internal use.
    """
    net = round(quantity * unit_price, 3)
    return {
        # ── PDF renderer keys ──────────────────────────────────────────────
        "sr":          sr,
        "description": product,
        "item_code":   item_code,
        "qty":         quantity,
        "uom":         unit,
        "unit_price":  unit_price,
        "discount":    0.0,
        "discount2":   0.0,
        "rate":        unit_price,   # rate = unit_price (no discount applied)
        "net_amount":  net,
        # ── Internal keys (stored in Invoice.line_items, used by routes) ──
        "product":     product,
        "quantity":    quantity,
        "unit":        unit,
    }


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
    ValueError  : Order not found / already billed / price missing with no override.
    Exception   : PDF write failure (DB record NOT saved in this case).
    """
    from app.services.invoice_generator import generate_invoice_pdf
    from app.services.amount_in_words import amount_in_words

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError(f"Order {order_id} not found")

    # Guard: already billed
    if getattr(order, "invoice_id", None) is not None:
        raise ValueError(f"Order {order_id} is already billed")

    parsed_items = _safe_list(order.parsed_items)

    # ── Build line items ──────────────────────────────────────────────────────
    if line_items_override is not None:
        # Normalise caller-supplied items into full PDF-ready shape
        line_items = []
        for idx, item in enumerate(line_items_override):
            product    = item.get("product", "")
            quantity   = float(item.get("quantity", 0))
            unit       = item.get("unit", "KGS")
            unit_price = float(item.get("unit_price", 0))
            item_code  = _get_item_code(product, db)
            line_items.append(
                _build_line_item(idx + 1, product, quantity, unit, unit_price, item_code)
            )
    else:
        line_items = []
        missing    = []
        for idx, item in enumerate(parsed_items):
            product  = item.get("product", "")
            quantity = float(item.get("quantity", 0))
            unit     = item.get("unit", "KGS")
            price    = get_price(order.customer_phone, product, db)
            if price is None:
                missing.append(product)
            else:
                item_code = _get_item_code(product, db)
                line_items.append(
                    _build_line_item(idx + 1, product, quantity, unit, price, item_code)
                )
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
    safe_phone   = (order.customer_phone or "").replace("+", "")
    date_tag     = invoice_date.replace("-", "")
    pdf_filename = f"{invoice_number_str}_{safe_phone}_{date_tag}.pdf"
    pdf_path     = os.path.join(INVOICE_DIR, pdf_filename)

    # ── Generate PDF FIRST — if this fails we must NOT commit to DB ───────────
    try:
        os.makedirs(INVOICE_DIR, exist_ok=True)
        invoice_data = {
            "invoice_number":    invoice_number_str,
            "invoice_date":      invoice_date,
            "customer_name":     order.customer_name or "",
            "customer_phone":    order.customer_phone or "",
            "place_of_supply":   "",
            "line_items":        line_items,
            "subtotal":          subtotal,
            "additional_charge": additional_charge,
            "round_off":         round_off,
            "total_amount":      total_amount,
            "due_amount":        due_amount,
            "amount_in_words":   amount_in_words(total_amount),
        }
        generate_invoice_pdf(invoice_data=invoice_data, output_path=pdf_path)
    except Exception as pdf_err:
        logger.error(
            "billing_service: PDF generation failed for order %s: %s",
            order_id, pdf_err, exc_info=True,
        )
        raise

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
    Find all unbilled orders for date_str, generate each.
    Uses savepoints so one failure does not abort the rest.
    Returns {"success": N, "failed": M, "errors": [...]}.
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

    for order in orders:
        sp = db.begin_nested()  # savepoint — isolates each invoice attempt
        try:
            create_invoice(
                order_id            = order.id,
                invoice_date        = date_str,
                line_items_override = None,
                db                  = db,
            )
            sp.commit()
            success += 1
        except Exception as e:
            sp.rollback()
            failed += 1
            msg = f"Order {order.id} ({order.customer_name}): {e}"
            errors.append(msg)
            logger.warning("billing_service bulk: %s", msg)

    logger.info(
        "billing_service bulk: success=%d failed=%d for date %s",
        success, failed, date_str,
    )
    return {"success": success, "failed": failed, "errors": errors}


# ── 6. Auto-invoice hook ───────────────────────────────────────────────────────

def try_auto_invoice(db: Session, order: Order) -> Optional[dict]:
    """
    Called from order_service after order save (behind feature flags).
    NEVER raises — all exceptions are caught and logged.
    Returns result dict on success, None on skip or failure.
    """
    if order.status != "confirmed":
        return None
    if getattr(order, "invoice_id", None) is not None:
        return None

    parsed_items = _safe_list(order.parsed_items)
    for item in parsed_items:
        product = item.get("product", "")
        if get_price(order.customer_phone, product, db) is None:
            logger.info(
                "billing_service auto: skipping order %s — prices missing for '%s'",
                order.id, product,
            )
            return None

    try:
        invoice_date = (
            order.business_date
            if order.business_date
            else datetime.now(IST).date().isoformat()
        )
        result = create_invoice(
            order_id            = order.id,
            invoice_date        = invoice_date,
            line_items_override = None,
            db                  = db,
        )
        # Patch generated_by to 'auto' — non-critical
        try:
            inv = db.query(Invoice).filter(Invoice.id == result["invoice_id"]).first()
            if inv:
                inv.generated_by = "auto"
                db.commit()
        except Exception:
            pass
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
    """Returns {"total_orders": N, "billed": N, "unbilled": N, "revenue": N}."""
    orders = (
        db.query(Order)
        .filter(
            Order.business_date == date_str,
            Order.is_cancelled.is_(False),
        )
        .all()
    )


    from app.models.invoice import Invoice
    
    billed_order_ids = {
        row[0] for row in
        db.query(Invoice.order_id)
        .filter(Invoice.status != "voided")
        .all()
    }
    
    total    = len(orders)
    billed   = sum(1 for o in orders if o.id in billed_order_ids)
    unbilled = total - billed
    

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
      "Billed"         — has invoice_id
      "Prices Missing" — one or more products have no price
      "Unbilled"       — all prices set but not yet billed
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
            billing_status = "Billed"
        else:
            parsed_items = _safe_list(order.parsed_items)
            any_missing = any(
                get_price(order.customer_phone, item.get("product", ""), db) is None
                for item in parsed_items
            )
            billing_status = "Prices Missing" if any_missing else "Unbilled"

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