"""
template_parser.py
------------------
Fuzzy template parser — accepts messy human input like:
  "wings - 2"  /  "Wings-2kg"  /  "breast boneless- 5 kg"
  "CC 3"  /  "lollipop-10 nos"  /  "td 5"  /  "kaleji 1kg"

Full Fluffy product catalog with Pune restaurant/hotel customer
aliases (English shortcodes + Hindi/Marathi slang + noisy variants).
"""

import re
import os

PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")

# ── Product Catalog ───────────────────────────────────────────────────────────
# Format: (Display Name, default unit, [aliases])
# All aliases must be lowercase.
# Order matters for ambiguous shortcodes — more specific products first.

PRODUCT_DEFINITIONS = [

    # ── Whole Chicken: Without Skin (listed before With Skin so
    #    "skinless" / "wos" / "clean" aliases don't accidentally
    #    fall through to the WS variants) ──────────────────────

    ("W/O Skin Tandoor Chicken", "nos", [
        # Full names
        "without skin tandoor", "without skin whole chicken tandoor",
        "wo skin tandoor", "w/o skin tandoor",
        # Skinless variants
        "skinless tandoor", "skinless tandoor chicken",
        "tandoor skinless", "skin out tandoor",
        # Clean variants
        "clean tandoor", "clean small chicken", "cleaned tandoor",
        # No skin variants
        "no skin tandoor", "no skin td",
        # Shortcodes
        "sl tandoor", "wos tandoor",
        # Noisy
        "skin remove tandoor",
    ]),

    ("W/O Skin Regular Chicken", "nos", [
        # Full names
        "without skin regular", "without skin whole chicken regular",
        "wo skin regular", "w/o skin regular",
        # Skinless variants
        "skinless regular", "skinless regular chicken",
        "regular skinless", "skin out regular",
        # Clean variants
        "clean regular", "clean big chicken", "cleaned regular",
        # No skin variants
        "no skin regular", "no skin reg",
        # Shortcodes
        "sl regular", "wos regular",
    ]),

    # ── Whole Chicken: With Skin ──────────────────────────────

    ("WS Tandoor Chicken", "nos", [
        # Full names
        "with skin whole chicken tandoor", "ws whole chicken tandoor",
        "with skin tandoor", "whole chicken tandoor",
        "ws tandoor chicken", "ws tandoor", "tandoor chicken",
        "skin tandoor",
        # Primary shortcodes
        "tandoor", "tandoori", "td", "tdr",
        # Size aliases
        "small chicken", "small chik", "1kg chicken", "1kg chik",
        "chota chicken", "chota chik",
        # Noisy variants
        "tandor", "tanduri", "tandoor cut", "tandoor size", "tandoor bird",
    ]),

    ("WS Regular Chicken", "nos", [
        # Full names
        "with skin whole chicken regular", "ws whole chicken regular",
        "with skin regular", "whole chicken regular",
        "ws regular chicken", "ws regular", "regular chicken",
        "skin regular",
        # Primary shortcodes
        "regular", "reg chicken", "reg chik",
        # Size aliases
        "big chicken", "big chik", "full chicken",
        "large chicken", "heavy chicken",
        "1.5kg chicken", "regular bird",
        # General whole chicken (fallback)
        "whole chicken", "whole broiler", "whole broiler chicken",
        "broiler", "boiler", "full bird", "wbc",
        # Hindi/Marathi
        "murgi", "murg", "kombdi", "kombadi",
        # Noisy variants
        "reguler", "reglar", "big bird", "large bird",
    ]),

    # ── Boneless ──────────────────────────────────────────────

    ("Breast Boneless", "kg", [
        # Full names
        "breast boneless", "boneless breast", "breast",
        "breast piece", "breast boneless piece", "chicken breast",
        # Shortcodes
        "bb", "cb",
        "bl breast", "breast bl", "b/l breast", "b.l breast",
        # Noisy variants
        "brest", "brest boneless", "breast bnls",
    ]),

    ("Leg Boneless", "kg", [
        # Full names
        "leg boneless", "boneless leg",
        "thigh boneless", "thigh", "boneless thigh",
        "chicken thigh", "dark meat",
        # Shortcodes
        "lb",
        "bl leg", "leg bl", "b/l leg", "b.l leg",
        # Noisy variants
        "leg bnls", "thigh bnls", "bonless leg",
    ]),

    # ── Wings ─────────────────────────────────────────────────

    ("Wings", "kg", [
        # Full names
        "wings", "wing", "chicken wings", "wing piece", "hot wings",
        # Noisy variants
        "wngs", "wingz",
    ]),

    # ── Ready Lollipop ────────────────────────────────────────

    ("Ready Lollipop", "nos", [
        # Full names
        "lollipop", "ready lollipop", "lollypop", "lolipop",
        "chicken lollipop", "ready lollypop",
        # Shortcodes
        "lp",
        # Noisy variants
        "loli", "lolypop",
    ]),

    # ── Bone Products ─────────────────────────────────────────

    ("Carcass", "nos", [
        # Full names
        "carcass", "carcus", "chicken carcass",
        "frame", "chicken frame",
        # Common aliases
        "haddi", "bones", "chicken bones", "bone",
        # Noisy variants
        "haddi chicken",
    ]),

    # ── Cut Variants ──────────────────────────────────────────

    ("Curry Cut", "kg", [
        # Full names
        "curry cut", "currycut", "curry cut chicken",
        "curry chicken", "curry piece", "curry pcs",
        # Shortcodes
        "cc", "c/c", "c.c",
        # Hindi/Marathi
        "kari cut", "kadi cut", "kari", "rassa cut", "rassa",
        # Noisy variants
        "cury", "curry",
    ]),

    ("Biryani Cut", "kg", [
        # Full names
        "biryani cut", "biryani cut chicken",
        "biryani chicken", "biryani pcs", "biryani piece",
        # Shortcodes
        "bc", "b/c", "brc",
        # Hindi/Marathi
        "biriyani cut", "biriyani",
        # Noisy variants
        "biryani cut piece", "biryani chik", "biryani",
    ]),

    # ── Leg Parts ─────────────────────────────────────────────

    ("Drumstick", "kg", [
        # Full names
        "drumstick", "drumsticks", "drum stick", "drum sticks",
        "chicken drumstick",
        # Shortcodes
        "ds", "drum",
        # Noisy variants
        "drmstk", "drumstik",
    ]),

    ("Whole Leg", "nos", [
        # Full names
        "whole leg", "full leg", "full chicken leg",
        "leg piece", "complete leg",
        # Shortcodes
        "wl",
        # Noisy variants
        "wholeleg", "fullleg",
    ]),

    # ── Organ Meat ────────────────────────────────────────────

    ("Liver", "kg", [
        # Full names
        "liver", "chicken liver", "liver piece",
        # Hindi/Marathi
        "kaleji", "kaleja",
        # Shortcodes
        "liv", "lvr",
        # Noisy variants
        "kaliji",
    ]),

    ("Gizzard", "kg", [
        # Full names
        "gizzard", "gizzards", "chicken gizzard", "gizzard piece",
        # Hindi/Marathi
        "gurda", "pota",
        # Shortcodes
        "giz", "gizz",
        # Noisy variants
        "gizerd",
    ]),

]


# ── Normalizers ───────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase, strip punctuation noise, trim whitespace."""
    return (
        text.lower()
        .replace("*", "")
        .replace("_", "")
        .replace("/", "")
        .replace(".", "")
        .replace("-", " ")
        .strip()
    )


def _squish(text: str) -> str:
    """Remove all whitespace — 'breast boneless' → 'breastboneless'."""
    return re.sub(r'\s+', '', _normalize(text))


def _tokenize(text: str) -> set:
    """Split normalized text into word tokens."""
    return set(_normalize(text).split())


def _match_product(raw_name: str):
    """
    Returns (display_name, unit) or None.

    Match priority:
    1. Exact alias match (normalized + squished)
    2. Partial / startswith match
    3. Token subset match (handles word-order variations)
    """
    n = _normalize(raw_name)
    s = _squish(raw_name)

    # 1. Exact match
    for display, unit, aliases in PRODUCT_DEFINITIONS:
        for alias in aliases:
            if n == _normalize(alias) or s == _squish(alias):
                return display, unit

    # 2. Partial / startswith match
    for display, unit, aliases in PRODUCT_DEFINITIONS:
        for alias in aliases:
            a = _normalize(alias)
            aq = _squish(alias)
            if n.startswith(a) or a.startswith(n):
                return display, unit
            if s.startswith(aq) or aq.startswith(s):
                return display, unit

    # 3. Token subset match (word order doesn't matter)
    raw_tokens = _tokenize(raw_name)
    if raw_tokens:
        for display, unit, aliases in PRODUCT_DEFINITIONS:
            for alias in aliases:
                alias_tokens = _tokenize(alias)
                if alias_tokens and (
                    raw_tokens.issubset(alias_tokens)
                    or alias_tokens.issubset(raw_tokens)
                ):
                    return display, unit

    return None


# ── Unit normalization ────────────────────────────────────────────────────────

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
    return UNIT_ALIASES.get(raw.lower().strip().rstrip("."), None)


def _parse_quantity(raw: str):
    try:
        qty = float(raw.strip())
        if qty <= 0:
            return None
        return qty
    except (ValueError, AttributeError):
        return None


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_template_order(customer_phone: str, message: str) -> dict:
    """
    Parse a free-form or template-style order message.

    Returns:
        {
            customer_phone, items, delivery_date, delivery_time,
            is_unclear, unclear_reason,
            errors: [{"line": str, "reason": str, "suggestion": str}]
        }
    """
    items         = []
    errors        = []
    delivery_time = None

    lines = message.strip().splitlines()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        lower = _normalize(line)

        # Delivery time line
        if lower.startswith("🕒") or "delivery time" in lower:
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

        # Strip placeholder tokens (__) but don't skip the line entirely —
        # customers often leave "__ 10 kg" instead of replacing the placeholder.
        line_clean = re.sub(r'__+', '', line).strip()

        # If no digits remain, this is an unfilled template line — skip silently.
        # e.g. "W/O Skin Tandoor (700-900g) -  nos" has no quantity, ignore it.
        if not line_clean or not re.search(r'\d', line_clean):
            continue

        # Pattern: <product name> <separator?> <quantity> [unit]
        split_match = re.match(
            r"^(.+?)\s*[-:]?\s*([\d\.]+)\s*(kg|kgs|kilo|kilos|kilogram|kilograms|nos|no|pcs|pc|pieces|piece|k)?\s*$",
            line_clean,
            re.IGNORECASE,
        )

        if not split_match:
            if len(line_clean) > 3:
                errors.append({
                    "line":       line,
                    "reason":     "Couldn't understand this line",
                    "suggestion": None,
                })
            continue

        raw_name     = split_match.group(1).strip()
        raw_qty      = split_match.group(2).strip()
        raw_unit_str = (split_match.group(3) or "").strip()
        raw_unit     = _normalize_unit(raw_unit_str) if raw_unit_str else None

        if raw_qty in ("__", "0", ""):
            continue

        product_match = _match_product(raw_name)
        if not product_match:
            errors.append({
                "line":       line,
                "reason":     f"*{raw_name}* is not in our product list",
                "suggestion": None,
            })
            continue

        display_name, expected_unit = product_match

        qty = _parse_quantity(raw_qty)
        if qty is None:
            errors.append({
                "line":       line,
                "reason":     f"Quantity *{raw_qty}* doesn't look right",
                "suggestion": f"{display_name} - 1 {expected_unit}",
            })
            continue

        # Unit mismatch — only flag if customer explicitly typed a wrong unit
        if raw_unit and raw_unit != expected_unit:
            errors.append({
                "line":       line,
                "reason":     f"*{display_name}* is ordered in *{expected_unit}* (you sent {raw_unit})",
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
    lines = ["⚠️ *Small issue with your order:*\n"]
    for e in errors:
        lines.append(f"❌ {e['reason']}")
        if e.get("suggestion"):
            lines.append(f"✅ Please send like:\n{e['suggestion']}\n")
    lines.append(
        "\nFor the full product list, type *order* to get the template again."
    )
    return "\n".join(lines)