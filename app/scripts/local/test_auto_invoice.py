"""
scripts/local/test_auto_invoice.py
------------------------------------
Triggers the auto-invoice path for a specific order ID, with optional
price seeding for a clean end-to-end local test.

Usage:
    python scripts/local/test_auto_invoice.py --order-id 42
    python scripts/local/test_auto_invoice.py --order-id 42 --set-prices
"""

import sys
import os
import argparse
from pathlib import Path

# ── Project root on sys.path ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

# ── Force billing feature flags ON for this local script only ─────────────────
# These override whatever is in .env so the script is self-contained.
os.environ["FLAG_BILLING_ENABLED"]       = "true"
os.environ["FLAG_BILLING_AUTO_INVOICE"]  = "true"

from app.database import SessionLocal
from app.models.order import Order

# ── Default prices matching the FAIZ KHATIK spec ─────────────────────────────
DEFAULT_PRICES = {
    "Chicken Feet":               {"price_per_unit": 45.00, "unit": "KGS"},
    "Chicken Liver and Gizzard":  {"price_per_unit": 38.00, "unit": "KGS"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test the auto-invoice pipeline for a given order ID."
    )
    parser.add_argument(
        "--order-id",
        type=int,
        required=True,
        help="Primary key of the Order row to invoice.",
    )
    parser.add_argument(
        "--set-prices",
        action="store_true",
        help=(
            "Seed default product prices before invoicing "
            "(Chicken Feet: 45.00/KGS, Chicken Liver and Gizzard: 38.00/KGS)."
        ),
    )
    return parser.parse_args()


# ── Billing module import (graceful fallback before billing is built) ─────────
try:
    from app.services.billing import try_auto_invoice, DefaultProductPrice  # type: ignore[import]
    BILLING_AVAILABLE = True
except ImportError:
    BILLING_AVAILABLE = False


def seed_prices(db, items: list) -> None:
    """
    Insert/update rows in default_product_prices for every product in the order.
    Uses DefaultProductPrice ORM model from app.services.billing.
    """
    if not BILLING_AVAILABLE:
        print("[seed_prices] billing module not available — skipping price seed.")
        return

    for item in items:
        product = item.get("product", "")
        if product not in DEFAULT_PRICES:
            print(f"  [seed_prices] No default price configured for {product!r} — skipping.")
            continue

        defaults = DEFAULT_PRICES[product]
        existing = (
            db.query(DefaultProductPrice)
            .filter(DefaultProductPrice.product_name == product)
            .first()
        )
        if existing:
            existing.price_per_unit = defaults["price_per_unit"]
            existing.unit            = defaults["unit"]
            print(f"  Updated price: {product}  {defaults['price_per_unit']:.2f} / {defaults['unit']}")
        else:
            row = DefaultProductPrice(
                product_name=product,
                price_per_unit=defaults["price_per_unit"],
                unit=defaults["unit"],
            )
            db.add(row)
            print(f"  Inserted price: {product}  {defaults['price_per_unit']:.2f} / {defaults['unit']}")

    db.commit()
    print()


def print_order(order: Order) -> None:
    print("─" * 48)
    print(f"Order #{order.id}")
    print(f"  customer_name  : {order.customer_name}")
    print(f"  customer_phone : {order.customer_phone}")
    print(f"  business_date  : {order.business_date}")
    print(f"  delivery_date  : {order.delivery_date}")
    print(f"  status         : {order.status}")
    print(f"  is_cancelled   : {order.is_cancelled}")
    print(f"  is_unclear     : {order.is_unclear}")
    print(f"  Items (parsed_items):")
    for item in (order.parsed_items or []):
        print(f"    • {item.get('product')}  {item.get('quantity')} {item.get('unit')}")
    print("─" * 48)
    print()


def main() -> None:
    args = parse_args()

    db = SessionLocal()
    try:
        # ── 1. Fetch the order ────────────────────────────────────────────────
        order = db.query(Order).filter(Order.id == args.order_id).first()
        if order is None:
            print(f"ERROR: No order found with id={args.order_id}.")
            print("Run seed_test_order.py first to create one.")
            sys.exit(1)

        print_order(order)

        # ── 2. Optionally seed prices ─────────────────────────────────────────
        if args.set_prices:
            print("Seeding default product prices …")
            seed_prices(db, order.parsed_items or [])

        # ── 3. Guard: billing module must exist ───────────────────────────────
        if not BILLING_AVAILABLE:
            print(
                "NOTICE: app.services.billing is not yet implemented.\n"
                "        Build the billing module, then re-run this script.\n"
                "        Expected import: from app.services.billing import try_auto_invoice"
            )
            sys.exit(0)

        # ── 4. Call try_auto_invoice ──────────────────────────────────────────
        # FLAG_BILLING_ENABLED + FLAG_BILLING_AUTO_INVOICE are already forced
        # to 'true' via os.environ at the top of this script.
        print(f"Calling try_auto_invoice(order_id={order.id}) …")
        print(f"  FLAG_BILLING_ENABLED      = {os.environ['FLAG_BILLING_ENABLED']}")
        print(f"  FLAG_BILLING_AUTO_INVOICE = {os.environ['FLAG_BILLING_AUTO_INVOICE']}")
        print()

        result = try_auto_invoice(db, order)

        # ── 5. Report result ──────────────────────────────────────────────────
        if result is None:
            print("No invoice generated (prices missing or order already billed).")
        else:
            print(f"✅ Invoice generated!")
            # result is expected to be a dict or object with at least a pdf_path
            if isinstance(result, dict):
                pdf_path = result.get("pdf_path") or result.get("path")
                invoice_id = result.get("invoice_id") or result.get("id")
            else:
                pdf_path   = getattr(result, "pdf_path", None)
                invoice_id = getattr(result, "id", None)

            if invoice_id:
                print(f"  invoice_id : {invoice_id}")
            if pdf_path:
                print(f"  PDF path   : {pdf_path}")
            else:
                print("  (no PDF path returned by try_auto_invoice)")

    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
