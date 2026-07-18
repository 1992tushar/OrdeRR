"""
Small, dependency-free shared helpers.

Imports only the standard library — safe to import from models, services and
routes without circular-import risk.
"""
import json


def safe_list(value) -> list:
    """Coerce an Order.parsed_items / unclear_items JSONB value into a list.

    Handles every shape the column takes across environments:
      • None                     → []
      • native list (JSONB)      → the list unchanged
      • JSON string (legacy)     → parsed list
      • double-encoded string    → inner list
      • "null" / "[]" / "" / junk → []

    Never raises.
    """
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        if value in ("null", "[]", ""):
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, str):          # double-encoded
                inner = json.loads(parsed)
                return inner if isinstance(inner, list) else []
        except Exception:
            pass
    return []


def fmt_qty(q) -> str:
    """Format a quantity for display — drop the decimal for whole numbers.

    3.0 → "3", 2.5 → "2.5", 10 → "10". Falls back to str(q) for anything
    non-numeric.
    """
    try:
        n = float(q)
    except (TypeError, ValueError):
        return str(q)
    return str(int(n)) if n == int(n) else str(n)
