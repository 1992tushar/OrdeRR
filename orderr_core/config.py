"""
Centralized runtime configuration.

Values still come from the environment (.env in development, Render env vars in
production) — this module just reads each one ONCE with a single, consistent
fallback, instead of every module calling os.getenv() with its own (previously
inconsistent) default. Imports only the standard library, so it is safe to
import from anywhere.

load_dotenv() runs first in main.py, so the environment is populated before
this module is imported.
"""
import os

# Plant / brand name shown in WhatsApp messages, reports and invoices.
PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")

# Operations manager's WhatsApp number (daily summaries, order alerts).
# Falls back to "" when unset so callers can safely do `if MANAGER_PHONE:`.
MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")

# Flat fine deducted from an employee's salary for each late mark (₹).
LATE_MARK_FINE = float(os.getenv("LATE_MARK_FINE", "200"))

# Gross-margin floor (%). Below this, the Financials screen + manager digest warn
# that selling rates are eroding margin vs purchase cost. Aggregate (buy whole
# birds → sell cuts), since per-SKU cost isn't maintained.
MARGIN_ALERT_PCT = float(os.getenv("MARGIN_ALERT_PCT", "20"))

# Public base URL of the app (Render) — used to build shareable links.
BASE_URL = os.getenv("BASE_URL", "")

# Path segment of the public live order-status page (/r/<key>). One static
# link shared by the manager and salespersons — no login, content follows the
# 9 PM business-date rollover.
REPORT_LINK_KEY = os.getenv("REPORT_LINK_KEY", "fluffy-status")


def report_url() -> str:
    """Absolute URL of the live order-status page (all salespersons)."""
    return f"{BASE_URL.rstrip('/')}/r/{REPORT_LINK_KEY}"


def sp_slug(name: str) -> str:
    """URL slug for a salesperson's personal status page — their first name,
    lowercased, letters/digits only (e.g. 'Ganesh Raundhal' → 'ganesh')."""
    first = (name or "").strip().split()[0] if (name or "").strip() else ""
    return "".join(ch for ch in first.lower() if ch.isalnum())


def sp_report_url(name: str) -> str:
    """Absolute URL of a salesperson's personal status page (their customers only)."""
    return f"{report_url()}/{sp_slug(name)}"
