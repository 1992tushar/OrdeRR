"""
Maintenance — scoped, destructive data reset.

Used by the token-gated admin reset endpoint to clear transactional + analytics
data on a fresh-start, while PRESERVING the master/people data (customers,
salespersons, staff). Uses an EXPLICIT clear-list (never an exclude-list) so the
preserved tables can never be hit by accident.

Table names are a hardcoded allowlist — no user input reaches the SQL.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

# Preserved as tables (rows kept). NOTE: customers rows are kept but their
# NON-identity fields are RESET (see reset_customer_fields) — only name / phone
# / area / salesperson (+ id, created_at) survive.
PRESERVE_TABLES = [
    "customers", "salespersons",
    "employees", "advances", "advance_repayments", "leaves",
]

# Customer columns kept intact; every other column is reset.
CUSTOMER_KEEP_FIELDS = ["id", "restaurant_name", "phone_number", "area",
                        "salesperson_id", "created_at"]

# Reset all non-identity customer fields to clean defaults (keeps the row
# usable: active, onboarded, daily). Clears financials, detail and ledger token.
_CUSTOMER_RESET_SQL = text("""
    UPDATE customers SET
        owner_name = NULL,
        address = NULL,
        city = NULL,
        outstanding = 0,
        credit_limit = NULL,
        onboarding_status = 'active',
        is_active = :yes,
        is_daily_order_customer = :yes,
        ledger_token = NULL
""")

# Cleared, children-first so FK constraints (enforced on Postgres) are satisfied.
CLEAR_TABLES = [
    # child rows first
    "invoice_items",
    "vasy_invoice_items",
    "vasy_purchase_items",
    "order_item_actuals",
    # OrdeRR operational
    "invoices",
    "orders",
    "inbound_messages",
    "customer_product_stats",
    "customer_product_aliases",
    "unclear_item_aliases",
    "noise_phrases",
    "ocr_unmatched_lines",
    "rate_unclear_queue",
    # pricing config
    "customer_rate_overrides",
    "daily_rates",
    # Vasy analytics mirrors + audit
    "customer_receipts",
    "outstanding_snapshots",
    "vasy_invoices",
    "vasy_purchases",
    "vasy_expenses",
    "vasy_payments",
    "vasy_supplier_bills",
    "import_logs",
]


def reset_transactional_data(db: Session, confirm: bool = False) -> dict:
    """Count (and if confirm=True, delete) rows in every CLEAR_TABLES table, and
    reset non-identity fields on customers (rows kept, only the 4 identity
    fields + id/created_at survive).

    Returns {"tables": {table: row_count}, "customers_reset": n}. Dry-run counts
    what would happen; confirm performs it. Never deletes PRESERVE_TABLES rows.
    All-or-nothing: on confirm, everything commits together; any error rolls back.
    """
    counts = {}
    try:
        for t in CLEAR_TABLES:
            n = db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() or 0
            counts[t] = int(n)
            if confirm and n:
                db.execute(text(f"DELETE FROM {t}"))
        customers_n = db.execute(text("SELECT COUNT(*) FROM customers")).scalar() or 0
        if confirm:
            db.execute(_CUSTOMER_RESET_SQL, {"yes": True})
            db.commit()
    except Exception:
        db.rollback()
        raise
    return {"tables": counts, "customers_reset": int(customers_n)}
