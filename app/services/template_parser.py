"""
template_parser.py
------------------
Fuzzy template parser — accepts messy human input like:
  "wings - 2"  /  "Wings-2kg"  /  "breast boneless- 5 kg"
  "CC 3"  /  "lollipop-10 nos"  /  "td 5"  /  "kaleji 1kg"

Full Fluffy product catalog with Pune restaurant/hotel customer
aliases (English shortcodes + Hindi/Marathi slang + noisy variants).

v2 changes:
- Accepts optional `db` session to check unclear_item_aliases before flagging unclear
- Returns `unclear_items` list (raw strings that couldn't be parsed) separately from `items`
- `is_unclear` is only True when ZERO items parsed (whole order unreadable)
- Partial orders (some parsed, some not) are accepted — unclear_items stored separately

v3 changes:
- Noise filtering: skips header/footer lines like restaurant name, dates,
  "ORDER FOR THE DAY", "kg( 900 gm size)" annotations, emoji-only lines etc.
"""

import re
import os
from app.models.customer_product_alias import CustomerProductAlias
from app.models.unclear_item_alias import UnclearItemAlias
from app.models.noise_phrase import NoisePhrase

PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")

# ── Product Catalog ───────────────────────────────────────────────────────────
# Format: (Display Name, default unit, [aliases])
# All aliases must be lowercase.
# Order matters for ambiguous shortcodes — more specific products first.

PRODUCT_DEFINITIONS = [

    # ── Whole Chicken: Without Skin ───────────────────────────────────────────

    ("W/O Skin Tandoor Chicken", "nos", [
        "without skin tandoor", "without skin whole chicken tandoor",
        "wo skin tandoor", "w/o skin tandoor",
        "skinless tandoor", "skinless tandoor chicken",
        "tandoor skinless", "skin out tandoor",
        "clean tandoor", "clean small chicken", "cleaned tandoor",
        "no skin tandoor", "no skin td",
        "sl tandoor", "wos tandoor",
        "al faham", "al-faham", "alfaham",
        "skin remove tandoor","तंदूर", "तंदूरी", "तंदूर चिकन", "तंदूरी चिकन", "तंदूर साइज", "तंदूर बर्ड",
    ]),

    ("W/O Skin Regular Chicken", "kg", [
        "without skin regular", "without skin whole chicken regular",
        "wo skin regular", "w/o skin regular",
        "whole chicken without skin", "whole chicken no skin",
        "whole chicken skin out", "whole chicken skinless",
        "skinless regular", "skinless regular chicken",
        "regular skinless", "skin out regular",
        "clean regular", "clean big chicken", "cleaned regular",
        "no skin regular", "no skin reg",
        "sl regular", "wos regular",
    ]),

   
    ("WS Regular Chicken", "kg", [
        "with skin whole chicken regular", "ws whole chicken regular",
        "with skin regular", "whole chicken regular",
        "ws regular chicken", "ws regular", "regular chicken",
        "skin regular",
        "regular", "reg chicken", "reg chik",
        "big chicken", "big chik", "full chicken",
        "large chicken", "heavy chicken",
        "1.5kg chicken", "regular bird",
        "whole chicken", "whole broiler", "whole broiler chicken",
        "broiler", "boiler", "full bird", "wbc",
        "murgi", "murg", "kombdi", "kombadi",
        "reguler", "reglar", "big bird", "large bird",
    ]),

    # ── Boneless ──────────────────────────────────────────────────────────────

    ("Breast Boneless", "kg", [
        "breast boneless", "boneless breast", "breast",
        "breast piece", "breast boneless piece", "chicken breast",
        "chest boneless", "chest bonless", "cast bonlas",
        "berst boneless", "berst",
        "bonless", "boneless",
        "bb", "cb",
        "bl breast", "breast bl", "b/l breast", "b.l breast",
        "brest", "brest boneless", "breast bnls","bonlesh", "bonles", "bonless chicken", "bonlesh chicken",
    ]),

    ("Leg Boneless", "kg", [
        "leg boneless", "boneless leg",
        "thigh boneless", "thigh", "boneless thigh",
        "chicken thigh", "dark meat",
        "lag bonlas", "leg bonless", "leg bnls", "bonless leg",
        "lb",
        "bl leg", "leg bl", "b/l leg", "b.l leg",
    ]),

    # ── Wings ─────────────────────────────────────────────────────────────────

    ("Wings", "kg", [
        "wings", "wing", "chicken wings", "wing piece", "hot wings",
        "wngs", "wingz", "lollipop", "ready lollipop", "lollypop", "lolipop",
        "chicken lollipop", "ready lollypop",
        "lp", "lpop",
        "loli", "lolypop",
    ]),

    # ── Ready Lollipop ────────────────────────────────────────────────────────

    ("Ready Lollipop", "kg", [
        "ready lollipop", 
        "ready lollypop",
    ]),

    # ── Bone Products ─────────────────────────────────────────────────────────

    ("Carcass", "kg", [
        "carcass", "carcus", "chicken carcass",
        "frame", "chicken frame",
        "haddi", "bones", "chicken bones", "bone",
        "cat pis", "cat piece", "cat pieces",
        "haddi chicken",
    ]),

    # ── Cut Variants ──────────────────────────────────────────────────────────

    ("Curry Cut", "kg", [
        "curry cut", "currycut", "curry cut chicken",
        "curry chicken", "curry piece", "curry pcs",
        "cc", "c/c", "c.c",
        "kari cut", "kadi cut", "kari", "rassa cut", "rassa",
        "thali", "thali chicken", "thali cut",
        "chikn", "cury", "curry",
        "pcs",
    ]),

    ("Biryani Cut", "kg", [
        "biryani cut", "biryani cut chicken",
        "biryani chicken", "biryani pcs", "biryani piece",
        "bc", "b/c", "brc",
        "biriyani cut", "biriyani",
        "biryani cut piece", "biryani chik", "biryani",
    ]),

    # ── Leg Parts ─────────────────────────────────────────────────────────────

    ("Drumstick", "kg", [
        "drumstick", "drumsticks", "drum stick", "drum sticks",
        "chicken drumstick",
        "ds", "drum", "drums",
        "drmstk", "drumstik", "darmistk",
    ]),

    ("Whole Leg", "nos", [
        "whole leg", "full leg", "full chicken leg",
        "leg piece", "complete leg",
        "wl",
        "tangdi", "tangadi", "t leg",
        "wholeleg", "fullleg","leg piece", "leg pcs", "leg pscs", "leg psc",
    ]),

    # ── Organ Meat ────────────────────────────────────────────────────────────

    ("Liver", "kg", [
        "liver", "chicken liver", "liver piece",
        "kaleji", "kaleja", "kalgi",
        "liv", "lvr",
        "kaliji",
    ]),

    ("Gizzard", "kg", [
        "gizzard", "gizzards", "chicken gizzard", "gizzard piece",
        "gurda", "pota",
        "giz", "gizz",
        "gizerd",
    ]),

    # ── Mince ─────────────────────────────────────────────────────────────────

    ("Kheema", "kg", [
        "kheema", "keema", "chicken kheema", "chicken keema",
        "mince", "chicken mince", "minced chicken",
        "khima", "qeema",
    ]),

]

# Flat set of all valid canonical product names (for alias resolution validation)
VALID_PRODUCT_NAMES = {d for d, _, _ in PRODUCT_DEFINITIONS}


# ── Normalizers ───────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
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
    return re.sub(r'\s+', '', _normalize(text))


def _tokenize(text: str) -> set:
    return set(_normalize(text).split())


def _match_product(raw_name: str):
    """
    Returns (display_name, unit) or None.
    Priority: exact → partial/startswith → token subset
    """
    n = _normalize(raw_name)
    s = _squish(raw_name)

    # 1. Exact
    for display, unit, aliases in PRODUCT_DEFINITIONS:
        for alias in aliases:
            if n == _normalize(alias) or s == _squish(alias):
                return display, unit

    # 2. Partial / startswith
    for display, unit, aliases in PRODUCT_DEFINITIONS:
        for alias in aliases:
            a  = _normalize(alias)
            aq = _squish(alias)
            if n.startswith(a) or a.startswith(n):
                return display, unit
            if s.startswith(aq) or aq.startswith(s):
                return display, unit

    # 3. Token subset
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


def _lookup_alias(raw_name: str, db) -> tuple | None:
    if db is None:
        return None
    
    # Try exact match first
    normalized = raw_name.strip().lower()
    row = db.query(UnclearItemAlias).filter(
        UnclearItemAlias.raw_text == normalized
    ).first()
    if row:
        unit = _get_unit_for_canonical(row.canonical_product_name)
        return (row.canonical_product_name, unit)

    # Strip quantity/unit suffix and try again
    # Handles "tandoori chicken 30pis" → "tandoori chicken"
    stripped = re.sub(r'\s*[-:]?\s*[\d\.]+\s*[a-zA-Z]*\s*$', '', normalized).strip()
    if stripped and stripped != normalized:
        row = db.query(UnclearItemAlias).filter(
            UnclearItemAlias.raw_text == stripped
        ).first()
        if row:
            unit = _get_unit_for_canonical(row.canonical_product_name)
            return (row.canonical_product_name, unit)

    return None

def _get_unit_for_canonical(canonical_name: str) -> str:
    """Find the unit for a canonical product name from PRODUCT_DEFINITIONS."""
    for display_name, unit, _ in PRODUCT_DEFINITIONS:
        if display_name.lower() == canonical_name.lower():
            return unit
    return "kg"  # default fallback

def _lookup_customer_alias(raw_name: str, customer_phone: str, db) -> tuple | None:
    """
    Check customer_product_aliases for a phone + raw_text match.
    Returns (canonical_product_name, unit) or None.
    db can be None (caller doesn't always have a session).
    """
    if db is None or not customer_phone:
        return None
    normalized = raw_name.strip().lower()
    row = db.query(CustomerProductAlias).filter(
        CustomerProductAlias.customer_phone == customer_phone,
        CustomerProductAlias.raw_text == normalized,
    ).first()
    if not row:
        return None
    # Find the unit for this canonical product (same logic as _lookup_alias)
    for display_name, unit, aliases in PRODUCT_DEFINITIONS:
        if display_name.lower() == row.canonical_product_name.lower():
            return (row.canonical_product_name, unit)
    # Fallback: return with default unit kg
    return (row.canonical_product_name, "kg")


def _is_noise_phrase(raw_name: str, db) -> bool:
    if db is None:
        return False
    normalized = raw_name.strip().lower()
    # Try exact match
    row = db.query(NoisePhrase).filter(NoisePhrase.raw_text == normalized).first()
    if row:
        return True
    # Try after stripping quantity suffix
    stripped = re.sub(r'\s*[-:]?\s*[\d\.]+\s*[a-zA-Z]*\s*$', '', normalized).strip()
    if stripped and stripped != normalized:
        row = db.query(NoisePhrase).filter(NoisePhrase.raw_text == stripped).first()
        if row:
            return True
    return False


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
    "pies":       "nos",
    "psc":       "nos",
    "pc":        "nos",
    "pis":       "nos",
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


# ── Noise filtering ───────────────────────────────────────────────────────────

# Date patterns: 10/06/2026  |  10-06-26  |  10/06  |  June 10  |  10 June
_DATE_LINE_RE = re.compile(
    r'^[\d]{1,2}[\/\-\.][\d]{1,2}([\/\-\.][\d]{2,4})?$'
    r'|^[\d]{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*$'
    r'|^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+[\d]{1,2}',
    re.IGNORECASE,
)

import re
 
# Matches lines where quantity comes FIRST: "3 तंदूर", "4 Leg piece", "2 lollipop"
_QTY_FIRST_RE = re.compile(
    r"^([\d]+(?:[./][\d]+)?)\s+(.+)$"
)


# Common header/footer filler phrases customers add
_FILLER_RE = re.compile(
    r'^(order\s+for(\s+the\s+day)?'
    r'|daily\s+order'
    r'|today[\'s]*\s+order'
    r'|order\s+of\s+the\s+day'
    r'|good\s+morning'
    r'|good\s+evening'
    r'|good\s+afternoon'
    r'|good\s+night'
    r'|please\s+find'
    r'|kindly\s+(note|send|arrange)'
    r'|hi\s*,?$'
    r'|hello\s*,?$'
    r'|dear\s+'
    r'|greetings'
    r'|thank\s+you'
    r'|thanks'
    r'|regards'
    r'|warm\s+regards'
    r'|as\s+usual'
    r'|same\s+as\s+(yesterday|last\s+(time|order))'
    r'|hotel\s+order$'
    r'|order\s+list$'
    r'|todays\s+order)',
    re.IGNORECASE,
)

# Lines starting with a unit word are size annotations, not order lines
# e.g. "kg( 900 gm size)"  /  "pcs (big)"
_UNIT_ANNOTATION_RE = re.compile(
    r'^(kg|kgs|gm|gms|gram|grams|nos|pcs|pc|psc|pies)\b',
    re.IGNORECASE,
)


def _strip_emojis(text: str) -> str:
    """Remove emoji characters, returning plain text."""
    return re.sub(
        r'[\U00010000-\U0010ffff'   # supplementary multilingual plane
        r'\U0001F300-\U0001F9FF'    # misc symbols & pictographs
        r'\u2600-\u26FF'            # misc symbols
        r'\u2700-\u27BF'            # dingbats
        r']+',
        '',
        text,
        flags=re.UNICODE,
    ).strip()


def _is_noise_line(line: str, restaurant_name_norm: str | None) -> bool:
    """
    Return True if this line is a header/footer/annotation and should be
    skipped entirely — not treated as an unclear item.

    Handles:
      • Restaurant name used as header/footer  e.g. "Test hotel 10", "Amrai hotel Order 🏨"
      • Date-only lines                         e.g. "10/06/2026", "10/06"
      • Filler phrases                          e.g. "ORDER FOR THE DAY", "Good morning"
      • Unit-annotation lines                   e.g. "kg( 900 gm size)"
      • Emoji/symbol-only lines                 e.g. "🏨🌟"
      • Numbered-list header lines              e.g. "1." alone with no product text
    """
    stripped = line.strip()
    if not stripped:
        return True

    # Strip emojis for text-based checks
    text_only = _strip_emojis(stripped)

    # 1. Nothing left after removing emojis → purely decorative line
    if not text_only:
        return True

    # 2. No alphanumeric content at all
    if not re.search(r'[a-zA-Z0-9]', text_only):
        return True

    norm = _normalize(text_only)

    # 3. Date-only line
    if _DATE_LINE_RE.match(norm.strip()):
        return True

    # 4. Filler header/footer phrase
    if _FILLER_RE.match(norm.strip()):
        return True

    # 5. Unit-annotation line (e.g. "kg( 900 gm size)")
    if _UNIT_ANNOTATION_RE.match(norm.strip()):
        return True

    # 6. Restaurant name appears anywhere in the line
    #    Covers: "Test hotel 10"  /  "Amrai hotel Order 🏨"  /  footer variants
    if restaurant_name_norm and restaurant_name_norm in norm:
        return True

    return False


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_template_order(customer_phone: str, message: str, db=None) -> dict:
    """
    Parse a free-form or template-style order message.

    Args:
        customer_phone: sender's phone number
        message:        raw WhatsApp message text
        db:             optional SQLAlchemy Session — used to check alias table.
                        Pass None to skip alias lookup (e.g. in tests).

    Returns:
        {
            customer_phone,
            items,           ← successfully parsed items
            unclear_items,   ← list of raw strings that couldn't be parsed
                               (after alias check) — stored on Order for manager review
            delivery_date,
            delivery_time,
            is_unclear,      ← True ONLY when items == [] (total failure)
            unclear_reason,
            errors           ← internal detail list (unit mismatches etc.)
        }
    """
    items          = []
    unclear_items  = []
    errors         = []
    delivery_time  = None

    # ── ONE-TIME: resolve restaurant name for noise filtering ─────────────────
    restaurant_name_norm = None
    if db:
        try:
            from app.models.customer import Customer
            from app.services.customer_service import normalize_phone
            cust = db.query(Customer).filter(
                Customer.phone_number == normalize_phone(customer_phone)
            ).first()
            if cust and cust.restaurant_name:
                restaurant_name_norm = _normalize(cust.restaurant_name)
        except Exception:
            pass  # never let this crash the parser
    # ─────────────────────────────────────────────────────────────────────────

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

        # Skip header/instruction/note lines (existing hard-coded list)
        if any(skip in lower for skip in [
            "place your order", "copy below", "fill in", "example",
            "delete what", "delivery time", "fluffy", "order —",
            "note -", "note-",
        ]):
            continue

        # Skip noise lines (dates, hotel names, filler phrases etc.)
        if _is_noise_line(line, restaurant_name_norm):
            continue

        # Strip placeholder tokens
        line_clean = re.sub(r'__+', '', line).strip()

        # No digits → unfilled template line, skip silently
        if not line_clean or not re.search(r'\d', line_clean):
            continue

        # Handle "3k" style
        line_clean = re.sub(r'(\d+)\s*k\b', r'\1 kg', line_clean)

        # Strip leading list markers: "1)", "1.", "1-", "•", "-", "*"
        line_clean = re.sub(r'^[\d]+[)\.\-]\s*', '', line_clean).strip()
        line_clean = re.sub(r'^[•\-\*]\s*', '', line_clean).strip()

        # ── CHANGE 6: Primary regex, then qty-first fallback ──────────────────
        # Pattern: <product name> <separator?> <quantity> [unit]
        split_match = re.match(
            r"^(.+?)\s*[-:]?\s*([\d\.]+)\s*(kg|kgs|kilo|kilos|kilogram|kilograms|pies|nos|no|nos\.|pcs|psc|pc|pis|pieces|piece|k)?\s*$",
            line_clean,
            re.IGNORECASE,
        )

        if split_match:
            raw_name     = split_match.group(1).strip()
            raw_qty      = split_match.group(2).strip()
            raw_unit_str = (split_match.group(3) or "").strip()
            raw_unit     = _normalize_unit(raw_unit_str) if raw_unit_str else None
        else:
            # Fallback: quantity-first format e.g. "3 तंदूर", "4 Leg piece"
            qty_first_match = _QTY_FIRST_RE.match(line_clean)
            if qty_first_match:
                raw_qty      = qty_first_match.group(1).strip()
                raw_name     = qty_first_match.group(2).strip()
                raw_unit_str = ""
                raw_unit     = None
            else:
                # No quantity found at all — treat whole line as product name, qty=1
                raw_name     = line_clean
                raw_qty      = "1"
                raw_unit_str = ""
                raw_unit     = None
        # ─────────────────────────────────────────────────────────────────────

        if raw_qty in ("__", "0", ""):
            continue

        # ── CHANGE 5: Updated lookup chain ────────────────────────────────────
        # 1. Global catalog aliases
        product_match = _match_product(raw_name)

        # 2. Customer-specific alias  ← NEW
        if not product_match:
            product_match = _lookup_customer_alias(raw_name, customer_phone, db)

        # 3. Global unclear_item_aliases
        if not product_match:
            product_match = _lookup_alias(raw_name, db)

        # 4. Noise check — skip silently
        if not product_match:
            if _is_noise_phrase(raw_name, db):
                continue
            # 5. Truly unclear — store raw line for manager review
            unclear_items.append(line)
            continue
        # ─────────────────────────────────────────────────────────────────────

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
                "reason":     f"*{display_name}* is ordered in *{expected_unit}* (you sent {raw_unit_str})",
                "suggestion": f"{display_name} - {int(qty) if qty == int(qty) else qty} {expected_unit}",
            })
            continue

        final_unit = raw_unit if raw_unit else expected_unit

        # Merge duplicates
        for item in items:
            if item["product"] == display_name and item["unit"] == final_unit:
                item["quantity"] += qty
                break
        else:
            items.append({
                "product":  display_name,
                "quantity": qty,
                "unit":     final_unit,
            })

    # is_unclear = truly nothing parseable at all
    is_unclear = len(items) == 0 and len(unclear_items) == 0

    return {
        "customer_phone": customer_phone,
        "items":          items,
        "unclear_items":  unclear_items,
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