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

# Preserved — NEVER cleared. Documented for the endpoint's response.
PRESERVE_TABLES = [
    "customers", "salespersons",
    "employees", "advances", "advance_repayments", "leaves",
]

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
    """Count (and if confirm=True, delete) rows in every CLEAR_TABLES table.

    Returns {table: row_count} of what exists now (dry-run) or what was deleted
    (confirm). Never touches PRESERVE_TABLES. All-or-nothing: on confirm, the
    deletes commit together; any error rolls back.
    """
    counts = {}
    try:
        for t in CLEAR_TABLES:
            n = db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() or 0
            counts[t] = int(n)
            if confirm and n:
                db.execute(text(f"DELETE FROM {t}"))
        if confirm:
            db.commit()
    except Exception:
        db.rollback()
        raise
    return counts
