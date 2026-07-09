"""
Business-date helpers — the single source for the 8 PM IST day-rollover logic.

Imports only the standard library + orderr_core.constants, so it is free of
circular-import risk and can be used from any service or route. order_service
and pending_orders re-export these so their existing importers keep working.
"""
from datetime import date, datetime, timedelta

from orderr_core.constants import IST, RESET_HOUR


def get_today_ist() -> date:
    return datetime.now(IST).date()


def compute_business_date(created_at_utc: datetime) -> date:
    """Business (delivery) date for an order created at `created_at_utc`:
    orders placed at/after RESET_HOUR IST count toward the next day."""
    ist_time = created_at_utc.astimezone(IST)
    if ist_time.hour >= RESET_HOUR:
        return (ist_time + timedelta(days=1)).date()
    return ist_time.date()


def get_current_business_date() -> date:
    """Today's business (delivery) date, applying the RESET_HOUR rollover."""
    now_ist = datetime.now(IST)
    if now_ist.hour >= RESET_HOUR:
        return (now_ist + timedelta(days=1)).date()
    return now_ist.date()


def get_current_business_date_str() -> str:
    return get_current_business_date().strftime("%Y-%m-%d")


def get_delivery_date_str() -> str:
    return get_today_ist().strftime("%Y-%m-%d")


# pending_orders historically exposed this name; it is byte-identical to
# get_current_business_date. Kept as an alias for its importers.
get_delivery_date_for_now = get_current_business_date
