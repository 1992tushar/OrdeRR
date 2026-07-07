"""
invoice_generator.py — core billing logic for Orderr Billing.

Hold conditions (neither writes nor raises partial state):
  1. Any OrderItemActual for the order has confidence='needs_review'
     AND confirmed_by IS NULL  →  unverified physical quantity.
  2. Any product for the order appears in rate_unclear_queue with
     resolved_at IS NULL for the relevant business_date  →  rate not confirmed.

Invoice number: FLUFFY-YYYYMMDD-NNN (NNN resets to 001 each calendar day).
Sequence is derived inside the same transaction via MAX() + parse + increment.
Works identically on SQLite (local) and Postgres (prod) — no dialect-specific
sequence objects used.

rate_used is SNAPSHOTTED at generation time. Never recalculated retroactively.
amount = actual_quantity × rate_used  (full Decimal precision, no rounding).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from orderr_core.models.invoice import Invoice, InvoiceItem
from orderr_core.models.actuals import OrderItemActual
from orderr_core.models.rate_unclear import RateUnclearItem
from orderr_core.models.order import Order
from orderr_core.services.rate_lookup import get_rate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class InvoiceHoldError(Exception):
    """Raised when billing must be blocked. message is user-displayable."""
    pass


class InvoiceAlreadyExistsError(Exception):
    """Raised when an invoice already exists for this order_id."""
    pass


def generate_invoice(
    db: Session,
    order_id: int,
    customer_phone: str,
    business_date: date,
) -> Invoice:
    """
    Generate (and commit) a draft invoice for order_id.

    Raises:
        InvoiceAlreadyExistsError  — idempotency guard (unique constraint on order_id)
        InvoiceHoldError           — unverified actuals or unclear rates
        ValueError                 — no actuals found for order_id
    """
    # ── 0. Idempotency guard ────────────────────────────────────────────────
    existing = db.scalar(select(Invoice).where(Invoice.order_id == order_id))
    if existing:
        raise InvoiceAlreadyExistsError(
            f"Invoice {existing.invoice_number} already exists for order {order_id}."
        )

    # ── 0b. Authoritative customer ──────────────────────────────────────────
    # The invoice belongs to the order's customer — that is the single source of
    # truth. Callers pass a customer_phone, but fuzzy name-based lookups upstream
    # can resolve the wrong customer when two names collide (e.g. "SAIRAT BIRYANI"
    # vs "Sairat Biryani Ravet"). Always take the phone from the order itself so
    # the invoice record — and the per-customer rate lookup below — can never be
    # stamped with a different customer than the order.
    order = db.scalar(select(Order).where(Order.id == order_id))
    if order and order.customer_phone:
        if customer_phone and customer_phone != order.customer_phone:
            logger.warning(
                "generate_invoice: passed customer_phone %r != order.customer_phone %r "
                "for order %s — using the order's phone.",
                customer_phone, order.customer_phone, order_id,
            )
        customer_phone = order.customer_phone

    # ── 1. Load actuals ─────────────────────────────────────────────────────
    actuals: list[OrderItemActual] = db.scalars(
        select(OrderItemActual).where(OrderItemActual.order_id == order_id)
    ).all()

    if not actuals:
        raise ValueError(f"No actuals found for order_id={order_id}. Cannot generate invoice.")

    # ── 2. Hold: unverified actuals ─────────────────────────────────────────
    unverified = [
        a for a in actuals
        if a.confidence == "needs_review" and not a.confirmed_by
    ]
    if unverified:
        products = ", ".join(a.product for a in unverified)
        raise InvoiceHoldError(
            f"Cannot generate invoice: actuals not confirmed for [{products}]. "
            "Resolve in the Unclear Actuals queue before billing."
        )

    # ── 3. Hold: unclear rates ──────────────────────────────────────────────
    # Check whether any product in this order has an unresolved entry in the
    # rate_unclear_queue. The queue uses resolved_product (nullable) to store
    # the matched product name after resolution; before resolution it is NULL.
    # We match on raw_line containing the product name OR on resolved_product,
    # but the safest signal is: any unresolved row (resolved=False) whose
    # raw_line we cannot yet assign. Since we can't reliably reverse-match
    # raw_line → product, we block if ANY unresolved rows exist for today's
    # business_date — operator must clear the queue before billing runs.
    product_names = [a.product for a in actuals]
    unclear_rates = db.scalars(
        select(RateUnclearItem).where(
            RateUnclearItem.resolved == False,  # noqa: E712 — SQLAlchemy requires ==
            RateUnclearItem.business_date == business_date,
        )
    ).all()
    if unclear_rates:
        raise InvoiceHoldError(
            f"Cannot generate invoice: {len(unclear_rates)} unresolved rate line(s) "
            f"exist for {business_date}. Resolve all unclear rates before billing."
        )

    # ── 4. Build line items — resolve rates, hold on stale or missing ──────
    items_data: list[dict] = []
    subtotal = Decimal("0")

    for actual in actuals:
        qty = Decimal(str(actual.actual_quantity or actual.ordered_quantity))
        unit = actual.actual_unit or actual.ordered_unit

        rr = get_rate(
            db=db,
            product=actual.product,
            business_date=business_date,
            customer_phone=customer_phone,
        )

        # Hold: no rate exists at all
        if rr.source == "none" or not rr.found:
            raise InvoiceHoldError(
                f"Cannot generate invoice: no rate found for [{actual.product}]. "
                "Add a daily rate or customer override before billing."
            )

# Stale (prior-day) rates are billable — your rates don't change
        # daily, so "no rate today" just means "use the last one on file."
        if rr.source == "override":
            rate_source = "customer_override"
        elif rr.source == "stale_daily_rate":
            rate_source = "carried_forward_rate"
        else:
            rate_source = "daily_rate"

        rate = Decimal(str(rr.rate_per_unit))
        amount = qty * rate
        subtotal += amount

        items_data.append({
            "product": actual.product,
            "quantity": qty,
            "unit": unit,
            "rate_used": rate,
            "amount": amount,
            "rate_source": rate_source,
        })

    # ── 5. Invoice number — MAX() in same transaction ───────────────────────
    invoice_number = _next_invoice_number(db, business_date)

    # ── 6. Persist atomically ───────────────────────────────────────────────
    invoice = Invoice(
        invoice_number=invoice_number,
        order_id=order_id,
        customer_phone=customer_phone,
        business_date=business_date,
        subtotal=subtotal,
        total=subtotal,          # Phase 1: no GST / adjustments
        status="draft",
    )
    db.add(invoice)
    db.flush()  # get invoice.id before inserting items

    for item in items_data:
        db.add(InvoiceItem(
            invoice_id=invoice.id,
            **item,
        ))

    db.commit()
    db.refresh(invoice)
    return invoice


# ---------------------------------------------------------------------------
# Invoice number sequencing
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"^FLUFFY-\d{8}-(\d{3})$")


def _next_invoice_number(db: Session, business_date: date) -> str:
    """
    FLUFFY-YYYYMMDD-NNN, NNN resets to 001 each calendar day.

    Uses MAX(invoice_number) scoped to business_date.  Works on SQLite and
    Postgres without dialect-specific sequences.  The surrounding transaction
    (flushed before commit) provides the necessary write lock on SQLite;
    on Postgres the unique constraint on invoice_number prevents races.
    """
    date_str = business_date.strftime("%Y%m%d")
    prefix = f"FLUFFY-{date_str}-"

    max_num: Optional[str] = db.scalar(
        select(func.max(Invoice.invoice_number)).where(
            Invoice.invoice_number.like(f"{prefix}%")
        )
    )

    if max_num:
        m = _NUMBER_RE.match(max_num)
        nnn = int(m.group(1)) + 1 if m else 1
    else:
        nnn = 1

    return f"{prefix}{nnn:03d}"