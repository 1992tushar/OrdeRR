"""
ocr_actuals_parser.py
----------------------
Parses production-report extraction results into
(product, delivered_quantity, unit) actuals for billing.

v4 ARCHITECTURE — two entry points:

  parse_claude_hotel_rows(hotel_blocks)   ← PRIMARY path (v4)
      Consumes List[HotelBlock] from ClaudeProductionReportEngine.extract_rows().
      Each block has hotel_name + items (product rows).
      Returns a per-hotel dict so the route can save actuals against
      the correct order_id for each hotel.

  parse_gemini_rows(rows)                 ← LEGACY path (v3, flat list)
      Kept for any callers that still pass a flat list of product rows.

  parse_ocr_lines(lines)                  ← LEGACY path (v2, raw OCR lines)
      Kept for any non-actuals callers that feed raw OCR text lines.

OUTPUT CONTRACT for parse_claude_hotel_rows():
{
    "hotels": [
        {
            "hotel_name": str,          # as extracted by Claude
            "matched": [
                {
                    "product":                str,
                    "quantity":               float | None,
                    "ordered_quantity_hint":  float | None,
                    "unit":                   "kg" | "g" | "nos",
                    "needs_review":           bool,
                    "ocr_confidence":         float | None,   # 0–100
                    "review_reason":          str | None,
                },
                ...
            ],
            "unmatched": [
                {"raw_line": str, "reason": str},
                ...
            ],
        },
        ...
    ]
}

OUTPUT CONTRACT for parse_gemini_rows() and parse_ocr_lines() — unchanged from v3:
{
    "matched": [...],
    "unmatched": [...],
}
"""
from __future__ import annotations
from orderr_core.utils import fmt_qty

import logging
import re
from typing import Dict, List, Optional, Tuple

from orderr_core.services.rate_parser import _match_product, _normalize

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Shared constants
# ─────────────────────────────────────────────────────────────────────────────

GEMINI_CONFIDENCE_THRESHOLD = 0.70
OCR_CONFIDENCE_THRESHOLD = 70.0
_QTY_MAX_REASONABLE = 10_000
_DELIVERED_VS_ORDERED_MAX_RATIO = 3.0

_NORMALIZE_UNIT = {
    "kg":    "kg",
    "kgs":   "kg",
    "kilo":  "kg",
    "g":     "g",
    "gm":    "g",
    "gms":   "g",
    "grams": "g",
    "nos":   "nos",
    "no":    "nos",
    "pcs":   "nos",
    "pc":    "nos",
}


# ─────────────────────────────────────────────────────────────────────────────
# Shared row parser (used by both hotel and flat-list paths)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_product_row(row: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
    """
    Parse a single product row dict into either a matched item or an unmatched entry.

    Returns (matched_item, None) on success, (None, unmatched_entry) on failure.
    """
    raw_name    = str(row.get("product_name") or "").strip()
    ordered_qty = row.get("ordered_quantity")
    deliv_qty   = row.get("delivered_quantity")
    raw_unit    = str(row.get("unit") or "").strip().lower()
    confidence  = float(row.get("confidence") or 0.0)
    raw_notes   = str(row.get("raw_notes") or "").strip()

    if not raw_name:
        return None, None  # silently skip blank names

    product_match = _match_product(raw_name)
    if not product_match:
        return None, {
            "raw_line": raw_name,
            "reason": (
                f"Could not match '{raw_name}' to a known product "
                "(check spelling or add an alias in the catalog)"
            ),
        }

    display_name, default_unit = product_match
    unit = _NORMALIZE_UNIT.get(raw_unit) or default_unit
    ocr_confidence_pct = round(confidence * 100.0, 1)

    review_reasons: List[str] = []

    if confidence < GEMINI_CONFIDENCE_THRESHOLD:
        review_reasons.append(
            f"Low extraction confidence ({ocr_confidence_pct:.0f}%) — "
            "handwriting may be unclear, please verify against the photo."
        )
    if deliv_qty is None:
        review_reasons.append(
            "Delivered quantity not found on this row — cell may be blank "
            "or the handwriting was not readable. Enter manually."
        )
    if deliv_qty is not None:
        if deliv_qty <= 0 or deliv_qty > _QTY_MAX_REASONABLE:
            review_reasons.append(
                f"Delivered quantity {deliv_qty:g} is outside expected range "
                f"(0–{_QTY_MAX_REASONABLE:,}) — please verify."
            )
        elif (
            ordered_qty is not None
            and ordered_qty > 0
            and deliv_qty > ordered_qty * _DELIVERED_VS_ORDERED_MAX_RATIO
        ):
            review_reasons.append(
                f"Delivered quantity {deliv_qty:g} is more than "
                f"{_DELIVERED_VS_ORDERED_MAX_RATIO:.0f}× the ordered "
                f"quantity {ordered_qty:g} — please verify."
            )
    if raw_notes:
        review_reasons.append(f"Model note: {raw_notes}")

    matched_item = {
        "product":               display_name,
        "quantity":              deliv_qty,
        "ordered_quantity_hint": ordered_qty,
        "unit":                  unit,
        "needs_review":          bool(review_reasons),
        "ocr_confidence":        ocr_confidence_pct,
        "review_reason":         "  |  ".join(review_reasons) if review_reasons else None,
        "_confidence_raw":       confidence,  # used internally for dedup, stripped before return
    }
    return matched_item, None


def _dedup_matched(items: List[Dict]) -> List[Dict]:
    """
    Deduplicate matched items by product name.
    For duplicates: prefer the row with a real delivered_qty and higher confidence.
    Strips internal _confidence_raw key before returning.
    """
    seen: Dict[str, int] = {}
    result: List[Dict] = []

    for item in items:
        name = item["product"]
        conf = item.pop("_confidence_raw", 0.0)

        if name in seen:
            existing = result[seen[name]]
            existing_conf = existing.pop("_confidence_raw", 0.0)
            if (
                item["quantity"] is not None and existing["quantity"] is None
            ) or (
                item["quantity"] is not None
                and existing["quantity"] is not None
                and conf > existing_conf
            ):
                result[seen[name]] = item
            else:
                # restore the winner's conf (already popped above)
                existing["_confidence_raw"] = existing_conf
        else:
            seen[name] = len(result)
            result.append(item)

    # Final strip of any remaining _confidence_raw
    for item in result:
        item.pop("_confidence_raw", None)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# PRIMARY v4: parse_claude_hotel_rows()
# ─────────────────────────────────────────────────────────────────────────────

def parse_claude_hotel_rows(hotel_blocks: List[Dict]) -> Dict:
    """
    Convert List[HotelBlock] from ClaudeProductionReportEngine into a
    per-hotel matched/unmatched structure.

    Args:
        hotel_blocks: List[HotelBlock] as returned by
                      ClaudeProductionReportEngine.extract_rows().
                      Each dict has: hotel_name, items (List[ProductRow])

    Returns:
        {
            "hotels": [
                {
                    "hotel_name": str,
                    "matched":   [...],   # same item shape as parse_gemini_rows
                    "unmatched": [...],
                },
                ...
            ]
        }
    """
    result_hotels = []

    for block in hotel_blocks:
        hotel_name = str(block.get("hotel_name") or "").strip()
        if not hotel_name:
            logger.warning("Skipping hotel block with empty hotel_name.")
            continue

        raw_items = block.get("items") or []
        matched_raw: List[Dict] = []
        unmatched: List[Dict] = []

        for row in raw_items:
            matched_item, unmatched_entry = _parse_product_row(row)
            if matched_item:
                matched_raw.append(matched_item)
            elif unmatched_entry:
                unmatched.append(unmatched_entry)

        matched = _dedup_matched(matched_raw)

        logger.info(
            "parse_claude_hotel_rows: hotel=%r  matched=%d  unmatched=%d",
            hotel_name, len(matched), len(unmatched),
        )
        result_hotels.append({
            "hotel_name": hotel_name,
            "matched":    matched,
            "unmatched":  unmatched,
        })

    return {"hotels": result_hotels}


# ─────────────────────────────────────────────────────────────────────────────
# LEGACY v3: parse_gemini_rows()  — flat list, unchanged contract
# ─────────────────────────────────────────────────────────────────────────────

def parse_gemini_rows(rows: List[Dict]) -> Dict:
    """
    Convert a flat list of GeminiRow/ProductRow dicts into matched/unmatched.
    Kept for backward compatibility.
    """
    matched_raw: List[Dict] = []
    unmatched: List[Dict] = []

    for row in rows:
        matched_item, unmatched_entry = _parse_product_row(row)
        if matched_item:
            matched_raw.append(matched_item)
        elif unmatched_entry:
            unmatched.append(unmatched_entry)

    matched = _dedup_matched(matched_raw)

    logger.info(
        "parse_gemini_rows: %d matched, %d unmatched from %d input rows.",
        len(matched), len(unmatched), len(rows),
    )
    return {"matched": matched, "unmatched": unmatched}


# ─────────────────────────────────────────────────────────────────────────────
# LEGACY v2: parse_ocr_lines()  — raw OCR text lines, unchanged from v2
# ─────────────────────────────────────────────────────────────────────────────

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_UNIT_RE   = re.compile(r"\s*(kgs|kg|kilo|grams|gms|gm|g|nos|no|pcs|pc)\b", re.IGNORECASE)

_SKIP_LINE_SUBSTRINGS = (
    "dispatch", "daily production report", "fluffy", "total hotels",
    "generated", "product summary", "ordered qty", "delivered qty",
    "prepared by", "checked by", "accountant", "hotel-wise orders",
    "hotel wise orders",
    "product",
)

_HEADER_RE = re.compile(r"^\d+\.\s*\S+")
_DATE_WITH_MONTH_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b",
    re.IGNORECASE,
)


def _extract_name_and_numbers(line: str) -> Tuple[str, List[float]]:
    numbers = [float(m.group(0)) for m in _NUMBER_RE.finditer(line)]
    first_num_match = _NUMBER_RE.search(line)
    name = line[:first_num_match.start()].strip() if first_num_match else line.strip()
    name = name.rstrip("-: ").strip()
    return name, numbers


def _unit_after_number(line: str, number: float) -> Optional[str]:
    num_str = fmt_qty(number)
    idx = line.find(num_str)
    if idx == -1:
        return None
    m = _UNIT_RE.match(line[idx + len(num_str):])
    return m.group(1).lower() if m else None


def parse_ocr_lines(lines: List[Tuple[str, Optional[float]]]) -> Dict:
    """
    LEGACY entry point — unchanged from v2.

    Args:
        lines: [(line_text, ocr_confidence_0_to_100_or_None), ...]

    Returns the same matched/unmatched dict as parse_gemini_rows().
    """
    matched: List[Dict] = []
    unmatched: List[Dict] = []

    for raw_line, ocr_confidence in lines:
        line = (raw_line or "").strip()
        if not line:
            continue

        lower = _normalize(line)

        if _DATE_WITH_MONTH_RE.search(lower):
            continue
        if any(s in lower for s in _SKIP_LINE_SUBSTRINGS):
            continue
        if _HEADER_RE.match(line):
            continue
        if re.fullmatch(r"[\d/.\-\s:]+", line) or re.fullmatch(r"[^\w\s]+", line):
            continue

        name, numbers = _extract_name_and_numbers(line)

        if not name:
            unmatched.append({
                "raw_line": line,
                "reason": (
                    "Number found with no product name on the same line "
                    "(handwriting may have been detected separately from "
                    "its printed row — check this against the photo)."
                ),
            })
            continue

        if not numbers:
            unmatched.append({
                "raw_line": line,
                "reason": "Could not find a product name + quantity in this line",
            })
            continue

        product_match = _match_product(name)
        if not product_match:
            unmatched.append({
                "raw_line": line,
                "reason": f"Could not match '{name}' to a known product",
            })
            continue

        display_name, default_unit = product_match

        if len(numbers) == 1:
            matched.append({
                "product":               display_name,
                "quantity":              None,
                "ordered_quantity_hint": numbers[0],
                "unit":                  default_unit,
                "needs_review":          True,
                "ocr_confidence":        ocr_confidence,
                "review_reason": (
                    f"Only one number ({numbers[0]:g}) read on this line — "
                    "likely the printed Ordered Qty. Delivered Qty may be "
                    "blank or unreadable. Check the photo and enter manually."
                ),
            })
            continue

        delivered_qty = numbers[-1]
        unit = _unit_after_number(line, delivered_qty) or default_unit
        unit = _NORMALIZE_UNIT.get(unit, unit)
        unexpected_shape = len(numbers) > 2
        low_ocr_confidence = (
            ocr_confidence is not None and ocr_confidence < OCR_CONFIDENCE_THRESHOLD
        )
        needs_review = unexpected_shape or low_ocr_confidence

        review_reason = None
        if unexpected_shape:
            review_reason = f"Found {len(numbers)} numbers on this line, expected 2 — please verify."
        elif low_ocr_confidence:
            review_reason = f"Low OCR confidence ({ocr_confidence:.0f}%) on this line."

        for item in matched:
            if item["product"] == display_name:
                item.update({
                    "quantity":              delivered_qty,
                    "ordered_quantity_hint": item.get("ordered_quantity_hint"),
                    "unit":                  unit,
                    "needs_review":          needs_review,
                    "ocr_confidence":        ocr_confidence,
                    "review_reason":         review_reason,
                })
                break
        else:
            matched.append({
                "product":               display_name,
                "quantity":              delivered_qty,
                "ordered_quantity_hint": None,
                "unit":                  unit,
                "needs_review":          needs_review,
                "ocr_confidence":        ocr_confidence,
                "review_reason":         review_reason,
            })

    return {"matched": matched, "unmatched": unmatched}