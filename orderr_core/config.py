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
