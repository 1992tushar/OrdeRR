"""
scripts/local/seed_test_order.py
---------------------------------
Creates a confirmed test order in the LOCAL database matching the
FAIZ KHATIK invoice example.

Usage:
    python scripts/local/seed_test_order.py
    python scripts/local/seed_test_order.py --customer-name "Hotel Delicious" --phone 919999999999
"""

import sys
import os
import argparse
import json
from pathlib import Path

# ── Make sure the project root is on sys.path so app.* imports work ──────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env if present (mirrors how the app loads its config locally)
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass  # python-dotenv not installed — rely on real env vars

from app.database import SessionLocal
from app.models.order import Order
from app.services.order_service import get_current_business_date_str


# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_CUSTOMER_NAME  = "FAIZ KHATIK"
DEFAULT_CUSTOMER_PHONE = "919876543210"

# Items exactly matching the FAIZ KHATIK invoice spec
DEFAULT_ITEMS = [
    {"product": "Chicken Feet",                "quantity": 10, "unit": "KGS"},
    {"product": "Chicken Liver and Gizzard",   "quantity": 5,  "unit": "KGS"},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed a confirmed test order into the local OrdeRR database."
    )
    parser.add_argument(
        "--customer-name",
        default=DEFAULT_CUSTOMER_NAME,
        help=f"Customer name (default: {DEFAULT_CUSTOMER_NAME!r})",
    )
    parser.add_argument(
        "--phone",
        default=DEFAULT_CUSTOMER_PHONE,
        help=f"Customer WhatsApp phone (default: {DEFAULT_CUSTOMER_PHONE})",
    )
    return parser.parse_args()


def seed_order(customer_name: str, customer_phone: str) -> Order:
    db = SessionLocal()
    try:
        business_date = get_current_business_date_str()   # e.g. "2026-06-13"

        # Build a plausible raw_message so the record looks realistic
        raw_lines = "\n".join(
            f"{item['product']} - {item['quantity']} {item['unit']}"
            for item in DEFAULT_ITEMS
        )

        order = Order(
            plant_name=os.getenv("PLANT_NAME", "Fluffy"),
            customer_phone=customer_phone,
            customer_name=customer_name,
            raw_message=raw_lines,
            is_photo_order=False,
            # JSONB columns — pass native Python lists; SQLAlchemy serialises them
            parsed_items=DEFAULT_ITEMS,
            unclear_items=[],
            delivery_date=business_date,
            delivery_time=None,
            status="confirmed",          # billing scripts expect "confirmed"
            is_cancelled=False,
            is_unclear=False,
            unclear_reason=None,
            business_date=business_date,
            is_next_day_override=False,
            confirmation_sent=True,
            forwarded_to_manager=False,
        )

        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    args = parse_args()
    print(f"Seeding test order for {args.customer_name!r} ({args.phone}) …")

    order = seed_order(
        customer_name=args.customer_name,
        customer_phone=args.phone,
    )

    print(f"Seeded order id={order.id} for {order.customer_name}")
    print(f"  business_date : {order.business_date}")
    print(f"  status        : {order.status}")
    print(f"  items         :")
    for item in (order.parsed_items or []):
        print(f"    • {item['product']}  {item['quantity']} {item['unit']}")
    print()
    print(f"Run the invoice test with:")
    print(f"  python scripts/local/test_auto_invoice.py --order-id {order.id} --set-prices")


if __name__ == "__main__":
    main()
