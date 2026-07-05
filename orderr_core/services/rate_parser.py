"""
rate_parser.py
--------------
Parses forwarded WhatsApp "today's rate" messages, e.g.:

    Wings - 220
    Breast Boneless 260/kg
    CC: 180
    Tandoor - 210 per kg
    al faham 95/nos

This is a SEPARATE concern from orderr_core's order parsing (quantity of
items a customer wants), but it must use the SAME product-alias matching
pipeline so "CC", "tandoor", "al faham" etc. resolve to the same canonical
product names billing already stores rates against.

REUSE NOTE (architecture):
orderr_core does not expose a public rate-parsing API — the alias-matching
helpers (_match_product, _normalize, PRODUCT_DEFINITIONS) are underscore-
prefixed internals of orderr_core/services/template_parser.py. Per the
explicit Step 3 requirement to reuse "the same alias matching pipeline",
we import these private names directly rather than duplicating ~700 lines
of fuzzy-matching logic in billing. This is a deliberate, narrow exception
to normal public-API boundaries — billing still only ever READS from
orderr_core (never imports billing back), so the core architecture rule
("billing imports orderr_core, never the reverse") is intact. If
orderr_core's template_parser internals are refactored, this file
(rate_parser.py) is the only place that will need updating.

This module never writes ₹0 and never silently drops a line: every line
either becomes a confirmed (product, rate, unit) tuple or an unclear-line
string for the Rate Unclear queue.
"""
import re

from orderr_core.services.template_parser import (
    PRODUCT_DEFINITIONS,
    _match_product,   # noqa: reused intentionally, see module docstring
    _normalize,        # noqa: reused intentionally, see module docstring
)

# All canonical product display names + their default unit, in catalog order.
# Used by the dashboard to render "every active product".
ACTIVE_PRODUCTS: list[tuple[str, str]] = [
    (display, unit) for display, unit, _aliases in PRODUCT_DEFINITIONS
]


def _unit_for_product(display_name: str) -> str:
    for display, unit, _ in PRODUCT_DEFINITIONS:
        if display == display_name:
            return unit
    return "kg"


# Matches: "<name> <sep>? <number> [unit/rs/per-kg noise]"
# Mirrors the same "<name> <qty> [unit]" shape orderr_core's parser uses,
# but the trailing token here is a PRICE, not a quantity, so we strip any
# rupee/per-unit noise words rather than treating them as the unit itself.
_RATE_LINE_RE = re.compile(
    r"^(?P<name>.+?)\s*[-:]?\s*"
    r"(?:rs\.?|inr|₹)?\s*"
    r"(?P<rate>\d+(?:\.\d+)?)\s*"
    r"(?:/-?)?\s*"
    r"(?:rs\.?|inr|₹)?\s*"
    r"(?:per\s*)?(?P<unit>kg|kgs|kilo|nos|no|pcs|pc)?\.?\s*$",
    re.IGNORECASE,
)

_NORMALIZE_UNIT = {
    "kg": "kg", "kgs": "kg", "kilo": "kg",
    "nos": "nos", "no": "nos", "pcs": "nos", "pc": "nos",
}

_SKIP_LINE_SUBSTRINGS = (
    "rate list", "today's rate", "todays rate", "rates as on",
    "fluffy", "good morning", "gm ", "regards",
)


def parse_rate_message(message: str) -> dict:
    """
    Parses a forwarded rate message.

    Returns:
        {
            "confirmed": [
                {"product": str, "rate": float, "unit": "kg"|"nos"},
                ...
            ],
            "unclear": [
                {"raw_line": str, "reason": str},
                ...
            ],
        }

    Every non-blank, non-header line ends up in exactly one of the two
    lists above. Nothing is ever silently dropped.
    """
    confirmed: list[dict] = []
    unclear: list[dict] = []

    for raw_line in message.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue

        lower = _normalize(line)
        if any(s in lower for s in _SKIP_LINE_SUBSTRINGS):
            continue
        # Pure date/emoji/separator lines, e.g. "23/06/2026", "-----"
        if re.fullmatch(r"[\d/.\-\s:]+", line) or re.fullmatch(r"[^\w\s]+", line):
            continue

        match = _RATE_LINE_RE.match(line)
        if not match:
            unclear.append({
                "raw_line": line,
                "reason": "Could not find a product name + price in this line",
            })
            continue

        raw_name = match.group("name").strip()
        raw_rate = match.group("rate")
        raw_unit = (match.group("unit") or "").lower()

        if not raw_name:
            unclear.append({
                "raw_line": line,
                "reason": "No product name found before the price",
            })
            continue

        try:
            rate = float(raw_rate)
        except ValueError:
            unclear.append({
                "raw_line": line,
                "reason": f"Price '{raw_rate}' is not a valid number",
            })
            continue

        if rate <= 0:
            # Rule: NEVER ₹0. A zero/blank rate line is unclear, not confirmed.
            unclear.append({
                "raw_line": line,
                "reason": "Rate is ₹0 or missing — rates can never be confirmed at ₹0",
            })
            continue

        product_match = _match_product(raw_name)
        if not product_match:
            unclear.append({
                "raw_line": line,
                "reason": f"Could not match '{raw_name}' to a known product",
            })
            continue

        display_name, default_unit = product_match
        unit = _NORMALIZE_UNIT.get(raw_unit, default_unit)

        # Merge duplicates within the same message (last one wins, e.g. a
        # correction line later in the same forwarded message).
        for item in confirmed:
            if item["product"] == display_name and item["unit"] == unit:
                item["rate"] = rate
                break
        else:
            confirmed.append({"product": display_name, "rate": rate, "unit": unit})

    return {"confirmed": confirmed, "unclear": unclear}
