"""
template_parser.py
------------------
Fuzzy template parser — accepts messy human input like:
  "wings - 2"  /  "Wings-2kg"  /  "breast boneless- 5 kg"

Returns structured items + per-line error feedback for smart UX.
"""

import re
import os

PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")

# Canonical products: (display name, unit, list of fuzzy aliases)
PRODUCT_DEFINITIONS = [
    ("Whole Broiler Chicken", "kg", [
        "whole broiler chicken", "whole broiler", "broiler", "boiler",
        "full chicken", "whole chicken", "full bird",
    ]),
    ("Breast Boneless", "kg", [
        "breast boneless", "breast", "boneless breast",
        "breast bl", "bl breast",
    ]),
    ("Leg Boneless", "kg", [
        "leg boneless", "leg", "boneless leg",
        "leg bl", "bl leg",
    ]),
    ("Wings", "kg", [
        "wings", "wing",
    ]),
    ("Drumsticks", "kg", [
        "drumsticks", "drumstick", "drum sticks", "drum stick",
        "ds", "drumstik",
    ]),
]


def _normalize(text: str) -> str:
    return (
        text.lower()
        .replace("*", "")
        .replace("_", "")
        .strip()
    )


def _match_product(raw_name: str):
    """
    Returns (display_name, unit) or None.
    Tries exact alias match first, then partial/startswith.
    """
    n = _normalize(raw_name)
    # Exact alias match
    for display, unit, aliases in PRODUCT_DEFINITIONS:
        if n in aliases:
            return display, unit
    # Partial match — raw name starts with alias or alias starts with raw name
    for display, unit, aliases in PRODUCT_DEFINITIONS:
        for alias in aliases:
            if n.startswith(alias) or alias.startswith(n):
                return display, unit
    return None


# Unit normalization — whatever customer types → standard unit
UNIT_ALIASES = {
    "kg":        "kg",
    "kgs":       "kg",
    "kilo":      "kg",
    "kilos":     "kg",
    "kilogram":  "kg",
    "kilograms": "kg",
    "k":         "kg",
    "nos":       "nos",
    "no":        "nos",
    "nos.":      "nos",
    "pcs":       "nos",
    "pc":        "nos",
    "pieces":    "nos",
    "piece":     "nos",
    "number":    "nos",
    "numbers":   "nos",
}

def _normalize_unit(raw: str) -> str | None:
    """Returns canonical unit string or None if unrecognised."""
    return UNIT_ALIASES.get(raw.lower().strip().rstrip("."), None)


def _parse_quantity(raw: str):
    """
    Parse a quantity string to float. Returns None if invalid.
    e.g. "2" → 2.0, "2.5" → 2.5, "abc" → None
    """
    try:
        qty = float(raw.strip())
        if qty <= 0:
            return None
        return qty
    except (ValueError, AttributeError):
        return None

def parse_template_order(customer_phone: str, message: str) -> dict:
    """
    Parse a template-style order message.

    Returns:
        {
            customer_phone, items, delivery_date, delivery_time,
            is_unclear, unclear_reason,
            errors: [{"line": str, "reason": str, "suggestion": str}]
        }
    """
    items   = []
    errors  = []
    delivery_time = None

    lines = message.strip().splitlines()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # Delivery time line
        lower = _normalize(line)
        if lower.startswith("🕒") or lower.startswith("delivery time"):
            # Extract time after colon or dash
            time_match = re.search(r"[:\-]\s*(.+)$", line)
            if time_match:
                t = time_match.group(1).strip()
                if t and t not in ("__", "-", ""):
                    delivery_time = t
            continue

        # Skip header/instruction lines
        if any(skip in lower for skip in [
            "place your order", "copy below", "fill in", "example",
            "delete what", "delivery time", "fluffy", "order —",
        ]):
            continue

        # Skip placeholder lines like "Wings - __ kg"
        if "__" in line:
            continue

        # Try splitting on common separators: " - ", " : ", "-", ":"
        # Pattern: <product name> <separator> <quantity> [unit]
        split_match = re.match(
            r"^(.+?)\s*[-:]?\s*([\d\.]+)\s*(kg|kgs|kilo|kilos|kilogram|kilograms|nos|no|pcs|pc|pieces|piece|k)?\s*$",
            line,
            re.IGNORECASE,
        )

        if not split_match:
            # Couldn't parse the line at all — skip blanks/noise silently
            if len(line) > 3:
                errors.append({
                    "line":       line,
                    "reason":     "Couldn't understand this line",
                    "suggestion": None,
                })
            continue

        raw_name = split_match.group(1).strip()
        raw_qty  = split_match.group(2).strip()
        raw_unit_str = (split_match.group(3) or "").strip()
        raw_unit     = _normalize_unit(raw_unit_str) if raw_unit_str else None

        # Skip placeholder lines (__ or 0)
        if raw_qty in ("__", "0", ""):
            continue

        # Match product
        product_match = _match_product(raw_name)
        if not product_match:
            errors.append({
                "line":       line,
                "reason":     f"*{raw_name}* is not in our product list",
                "suggestion": None,
            })
            continue

        display_name, expected_unit = product_match

        # Parse quantity
        qty = _parse_quantity(raw_qty)
        if qty is None:
            errors.append({
                "line":       line,
                "reason":     f"Quantity *{raw_qty}* doesn't look right",
                "suggestion": f"{display_name} - 1 {expected_unit}",
            })
            continue

        # Unit mismatch check (only if customer explicitly specified a unit)
        if raw_unit and raw_unit != expected_unit:
            errors.append({
                "line":       line,
                "reason":     f"*{display_name}* must be in *{expected_unit}* (not {raw_unit})",
                "suggestion": f"{display_name} - {int(qty) if qty == int(qty) else qty} {expected_unit}",
            })
            continue

        # Merge duplicates
        for item in items:
            if item["product"] == display_name:
                item["quantity"] += qty
                break
        else:
            items.append({
                "product":  display_name,
                "quantity": qty,
                "unit":     expected_unit,
            })

    is_unclear = len(items) == 0

    return {
        "customer_phone": customer_phone,
        "items":          items,
        "delivery_date":  None,
        "delivery_time":  delivery_time,
        "is_unclear":     is_unclear,
        "unclear_reason": "No valid items found" if is_unclear else None,
        "errors":         errors,
    }


def build_error_message(errors: list) -> str:
    """
    Build a smart, copyable error feedback message.
    """
    lines = ["⚠️ *Small issue with your order:*\n"]
    for e in errors:
        lines.append(f"❌ {e['reason']}")
        if e.get("suggestion"):
            lines.append(f"✅ Please send like:\n{e['suggestion']}\n")
    lines.append(
        "\nFor the full product list, type *order* to get the template again."
    )
    return "\n".join(lines)