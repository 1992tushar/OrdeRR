#!/usr/bin/env python
"""
Regression suite for rate resolution + rate-aware invoice reissue.

WHY THIS EXISTS
---------------
Rates have two layers that are easy to get backwards: a GLOBAL daily rate that
applies to everyone and carries forward across days, and a per-CUSTOMER override
that must (a) beat the global rate for that hotel, (b) NOT leak to other hotels,
and (c) survive a global rate save. On top of that, an already-issued bill's
rate is frozen — the ONLY way to push a corrected rate into it is
reissue_invoice(refresh_rates=True). This file pins all of that down.

Runs against a THROWAWAY sqlite file (set before any orderr_core import) so it
can never touch the real local db or the production Postgres.

No pytest dependency — run directly:

    venv/Scripts/python tests/test_rate_billing.py
"""
import os
import sys
import tempfile

# Make the repo root importable when run as a bare script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# CRITICAL: pin the DB to a throwaway sqlite file BEFORE importing orderr_core,
# because orderr_core.database binds its engine to DATABASE_URL at import time
# (and the repo .env can point at production Postgres).
_TMP_DB = os.path.join(tempfile.gettempdir(), "orderr_test_rate_billing.sqlite")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"

from datetime import date, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

from orderr_core.database import Base, engine, SessionLocal  # noqa: E402
# Import model modules so their tables register on Base.metadata before create_all.
from orderr_core.models.daily_rate import DailyRate  # noqa: E402
from orderr_core.models.rate_override import CustomerRateOverride  # noqa: E402
from orderr_core.models.invoice import Invoice, InvoiceItem  # noqa: E402
from orderr_core.models.actuals import OrderItemActual  # noqa: E402
from orderr_core.services.rate_lookup import get_rate  # noqa: E402
from orderr_core.services.invoice_generator import reissue_invoice  # noqa: E402 (also registers Order / RateUnclearItem)

Base.metadata.create_all(engine)

_FAILURES = []


def check(label, got, want):
    if got != want:
        _FAILURES.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ok    {label}")


D = date(2026, 7, 18)
PHONE = "919999900001"
OTHER = "919999900002"
PROD = "Chicken Wings with Skin"


def seed():
    db = SessionLocal()
    # Global rate today = 100, and a prior-day rate = 90 (to prove carry-forward).
    db.add(DailyRate(product=PROD, business_date=D - timedelta(days=1),
                     rate_per_unit=Decimal("90"), unit="kg", source="dashboard", created_by="test"))
    db.add(DailyRate(product=PROD, business_date=D,
                     rate_per_unit=Decimal("100"), unit="kg", source="dashboard", created_by="test"))
    db.commit()
    db.close()


def test_rate_precedence():
    print("rate precedence")
    db = SessionLocal()

    # Global today's rate.
    check("global today", float(get_rate(db, PROD, D).rate_per_unit), 100.0)

    # Global carries forward to a later date with no new rate (source=stale).
    fut = get_rate(db, PROD, D + timedelta(days=3))
    check("carried-forward value", float(fut.rate_per_unit), 100.0)
    check("carried-forward flagged", fut.source, "stale_daily_rate")

    # Add a per-customer override — it must beat the global rate for that hotel.
    db.add(CustomerRateOverride(customer_phone=PHONE, product=PROD,
                                rate_per_unit=Decimal("80"), unit="kg",
                                effective_from=D, effective_to=None))
    db.commit()
    check("override beats global", float(get_rate(db, PROD, D, PHONE).rate_per_unit), 80.0)
    check("override source", get_rate(db, PROD, D, PHONE).source, "override")

    # The override must NOT leak to a different hotel.
    check("no leak to other hotel", float(get_rate(db, PROD, D, OTHER).rate_per_unit), 100.0)
    db.close()


def _make_invoice(db):
    """A hotel already invoiced at the global rate (100) for 2 kg = 200."""
    db.add(OrderItemActual(order_id=1, product=PROD, ordered_quantity=Decimal("2"),
                           ordered_unit="kg", actual_quantity=Decimal("2"),
                           actual_unit="kg", confidence="auto", confirmed_by="test"))
    inv = Invoice(invoice_number="FLUFFY-20260718-001", order_id=1, customer_phone=PHONE,
                  business_date=D, subtotal=Decimal("200"), total=Decimal("200"), status="draft")
    db.add(inv)
    db.flush()
    db.add(InvoiceItem(invoice_id=inv.id, product=PROD, quantity=Decimal("2"), unit="kg",
                       rate_used=Decimal("100"), amount=Decimal("200"), rate_source="daily_rate"))
    db.commit()


def test_reissue_refresh():
    print("reissue rate refresh")
    db = SessionLocal()
    _make_invoice(db)

    # An override of 80 now exists for this hotel (added in the precedence test),
    # but the issued bill's rate is frozen at 100.

    # refresh_rates=False keeps the ORIGINAL snapshot rate (quantity-only fix).
    inv = reissue_invoice(db, 1, refresh_rates=False)
    check("no-refresh keeps snapshot total", float(inv.total), 200.0)
    check("no-refresh keeps number", inv.invoice_number, "FLUFFY-20260718-001")

    # refresh_rates=True re-resolves every line — the override (80) now applies,
    # so 2 kg × 80 = 160, and the invoice number is UNCHANGED (no dup downstream).
    inv = reissue_invoice(db, 1, refresh_rates=True)
    check("refresh adopts override total", float(inv.total), 160.0)
    check("refresh keeps number", inv.invoice_number, "FLUFFY-20260718-001")
    check("refresh updates rate_source", inv.items[0].rate_source, "customer_override")
    db.close()


if __name__ == "__main__":
    seed()
    test_rate_precedence()
    test_reissue_refresh()
    print()
    if _FAILURES:
        print(f"FAILED — {len(_FAILURES)} check(s):")
        for f in _FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("All rate/billing checks passed.")
    sys.exit(0)
