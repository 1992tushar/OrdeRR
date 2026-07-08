"""
app/services/invoice_pdf.py

Generates a PDF invoice matching the Fluffy Fresh Foods / Vasy ERP paper layout.

Root-cause fix: the previous version defined 10 columns whose widths exceeded
the 174 mm content area. The "Rate" column was a duplicate of "Unit Price" and
pushed "Net Amount" off the right edge of the page (making it appear clipped as
"Nttamount" and causing the amount to display from the wrong column).

Correct layout: 9 columns, widths sum exactly to 174 mm.
  # (5) | Description (51) | Itemcode (22) | Qty (14) | UOM (10) |
  Unit Price (20) | Discount (15) | Discount2 (16) | Net Amount (21)
"""

from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import barcode as barcode_lib
from barcode.writer import ImageWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

if TYPE_CHECKING:
    from orderr_core.models.invoice import Invoice

# ── Company constants ────────────────────────────────────────────────────────
COMPANY_NAME     = "Fluffy Fresh Foods Private Limited"
COMPANY_ADDR     = ("At Malawadi, Near Kanifnath Mahraj Temple, "
                    "Talegaon chakan road, Talegaon Dabhade")
COMPANY_LINE2    = ("GSTIN NO : 27AAFCF3001L1ZU | "
                    "Email : fluffycustomercare@gmail.com | "
                    "Contact No. : 9623882123 | City : Pune |")
COMPANY_LINE3    = ("State : Maharashtra | Country : India  "
                    "Website: www.fluffymeat.com")
COMPANY_TAX_NOTE = ("Composition Taxable Person, "
                    "not eligible to collect tax on supplies")
PLACE_OF_SUPPLY  = "Maharashtra"
ITEM_CODE        = "CH0000000"
OUTPUT_DIR       = Path("invoices")

# Authorised-signature image. Drop the ERP's signature here (PNG, ideally with
# a transparent or white background) and it is embedded above the signature
# line automatically. Absent → just the blank line + "Authorised Signatory".
SIGNATURE_PATH   = Path("orderr_core/assets/signature.png")

# The ERP's "Due Amount" is the customer's running receivables balance. OrdeRR
# has no live payments ledger yet, so we use the `customers.outstanding`
# snapshot (imported from the Customer Outstanding sheet) as the prior balance
# and add the current invoice total on top:
#     Due Amount = customer.outstanding + invoice.total
# Fallback when the customer/outstanding is unavailable → just the invoice total.
# (see _lookup_outstanding + the totals section in generate_invoice_pdf)

# ── Page geometry ─────────────────────────────────────────────────────────────
# The invoice prints on HALF an A4 sheet (A4 torn across the middle) to save
# paper: 210 mm wide × 148.5 mm tall (landscape half). Width matches A4, so the
# 18 mm side margins still give a 174 mm content width.
#
# KNOWN LIMITATION: a half-A4 sheet fits ~4-5 line items comfortably; ~6+ items
# overflow the page and the signature/footer get clipped. There is no multi-page
# pagination yet (the ERP spills large invoices to a second half-sheet — "Next
# >>"). Add page-break handling here if invoices with many items become common.
PAGE_W = A4[0]        # 210 mm
PAGE_H = A4[1] / 2    # 148.5 mm — half of A4
ML = 18 * mm
MR = PAGE_W - 18 * mm
CW = MR - ML   # exactly 174 mm

# ── 10-Column table layout — matches the Vasy ERP paper invoice exactly ───────
# Columns: # | Description | Itemcode | Qty | UOM | Unit Price | Discount |
#          Discount2 | Rate | Net Amount.  Widths (mm) sum to exactly 174.
# Format: (header, width_mm, align).  Absolute x offsets are computed below.
_COL_DEFS = [
    ("#",            5,  "center"),
    ("Description", 44,  "left"),
    ("Itemcode",    20,  "center"),
    ("Qty",         13,  "right"),
    ("UOM",          9,  "center"),
    ("Unit Price",  17,  "right"),
    ("Discount",    14,  "right"),
    ("Discount2",   15,  "right"),
    ("Rate",        16,  "right"),
    ("Net Amount",  21,  "right"),
]
# Column index constants (keep row/total rendering readable & correct)
C_NUM, C_DESC, C_CODE, C_QTY, C_UOM, C_UNIT, C_DISC, C_DISC2, C_RATE, C_NET = range(10)

# Convert widths to absolute (x, width) positions in points, left-to-right.
COLS = []
_x_mm = 0
for _lbl, _w, _align in _COL_DEFS:
    COLS.append((_lbl, ML + _x_mm * mm, _w * mm, _align))
    _x_mm += _w
assert abs(_x_mm - 174) < 0.01, f"columns must sum to 174mm, got {_x_mm}"


def _fmt(value, decimals: int = 3) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    return f"{v:,.{decimals}f}"


# ── Amount in words (Indian numbering) ────────────────────────────────────────
_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
         "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
         "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety"]


def _two_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + ((" " + _ONES[n % 10]) if n % 10 else "")).strip()


def _three_words(n: int) -> str:
    """0..999 → words, with 'and' before the tens like the ERP does."""
    hundreds, rest = n // 100, n % 100
    parts = []
    if hundreds:
        parts.append(_ONES[hundreds] + " Hundred")
    if rest:
        parts.append(("and " if hundreds else "") + _two_words(rest))
    return " ".join(parts).strip()


def _amount_in_words(amount) -> str:
    """Indian-format rupees in words, e.g. 720 → 'Rupees Seven Hundred and
    Twenty Only'. Matches the Vasy ERP invoice wording."""
    try:
        rupees = int(Decimal(str(amount)))
    except Exception:
        rupees = 0
    if rupees == 0:
        return "Rupees Zero Only"
    crore, rupees = divmod(rupees, 10000000)
    lakh,  rupees = divmod(rupees, 100000)
    thou,  rupees = divmod(rupees, 1000)
    parts = []
    if crore:
        parts.append(_two_words(crore) + " Crore")
    if lakh:
        parts.append(_two_words(lakh) + " Lakh")
    if thou:
        parts.append(_two_words(thou) + " Thousand")
    if rupees:
        parts.append(_three_words(rupees))
    return "Rupees " + " ".join(p for p in parts if p).strip() + " Only"


def _buyer_phone(phone: str) -> str:
    """ERP prints the local 10-digit number; strip a leading 91 country code."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits


def _uom(unit: str) -> str:
    u = (unit or "").strip().lower()
    return {"kg": "KGS", "kgs": "KGS", "nos": "NOS", "no": "NOS"}.get(u, (unit or "").upper())


# ── ERP product master → (item code, full ERP description) ────────────────────
# Source: "Fluffy Fresh Foods Private Limited" product-list export. Keys are
# OrdeRR's canonical product names (lower-cased). Unmapped products fall back to
# the placeholder code + OrdeRR's own name, so nothing breaks.
PRODUCT_MAP = {
    "w/o skin tandoor chicken": ("CH1024558", "Without Skin whole chicken Tandoor"),
    "w/o skin regular chicken": ("CH1024559", "Without Skin whole chicken Regular"),
    "ws regular chicken":       ("CH1024561", "With Skin whole chicken Regular"),
    "curry cut":                ("CH1024563", "Chicken Curry Cut Without Skin"),
    "biryani cut":              ("CH1024576", "Chicken Biryani Cut"),
    "drumstick":                ("CH1024573", "Chicken Drumstick"),
    "whole leg":                ("CH1024572", "Chicken Whole Leg"),
    "liver":                    ("CH1024570", "Chicken Liver"),
    "gizzard":                  ("CH1024569", "Chicken Gizzard"),
    "kheema":                   ("CH1024571", "Chicken Kheema"),
    "breast boneless":          ("CH1024574", "Chicken Breast boneless"),
    "chicken breast":           ("CH1024574", "Chicken Breast boneless"),
    "leg boneless":             ("CH1024557", "Chicken Leg Boneless"),
    "wings":                    ("CH1024575", "Chicken Wings with Skin"),
    "carcass":                  ("CH1024567", "Chicken Carcass"),
}


def _product_info(product: str) -> tuple[str, str]:
    """Return (item_code, description) for a product — ERP values when mapped,
    otherwise the placeholder code and OrdeRR's own name."""
    info = PRODUCT_MAP.get((product or "").strip().lower())
    return info if info else (ITEM_CODE, product)


def _barcode_bytes(text: str) -> io.BytesIO:
    writer = ImageWriter()
    code = barcode_lib.get("code128", text, writer=writer)
    buf = io.BytesIO()
    code.write(buf, options={
        "module_width": 0.35,
        "module_height": 8.0,
        "write_text": False,
        "quiet_zone": 2,
        "dpi": 150,
    })
    buf.seek(0)
    return buf


def _lookup_address(customer_phone: str) -> str:
    """Return '<address>,<city>' for the customer, matching the ERP's
    'Address : ,Pune' style (empty address → just the city). Best-effort;
    never raises."""
    try:
        from orderr_core.database import SessionLocal
        from orderr_core.models.customer import Customer
        db = SessionLocal()
        try:
            cust = db.query(Customer).filter(
                Customer.phone_number == customer_phone
            ).first()
        finally:
            db.close()
        if cust:
            return f"{(cust.address or '').strip()},{(cust.city or '').strip()}"
    except Exception:
        pass
    return ""


def _lookup_outstanding(customer_phone: str) -> Decimal:
    """Return the customer's stored outstanding balance (prior receivables) as a
    Decimal. Best-effort — any failure or missing customer yields 0."""
    try:
        from orderr_core.database import SessionLocal
        from orderr_core.models.customer import Customer
        db = SessionLocal()
        try:
            cust = db.query(Customer).filter(
                Customer.phone_number == customer_phone
            ).first()
        finally:
            db.close()
        if cust and cust.outstanding is not None:
            return Decimal(str(cust.outstanding))
    except Exception:
        pass
    return Decimal("0")


def generate_invoice_pdf(invoice: "Invoice", hotel_name: str, address: str | None = None) -> str:
    """
    Render a branded A4 invoice PDF.

    Args:
        invoice:    Invoice ORM instance (with .items relationship loaded).
        hotel_name: Display name of the buyer / hotel.

    Returns:
        Absolute path to the saved PDF file (str).
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = hotel_name.strip().replace(" ", "_").replace("/", "-")
    out_path = OUTPUT_DIR / f"{safe_name}_{invoice.invoice_number}.pdf"

    c = canvas.Canvas(str(out_path), pagesize=(PAGE_W, PAGE_H))

    # ── drawing helpers ───────────────────────────────────────────────────────
    def hline(y: float, lw: float = 0.6) -> None:
        c.setLineWidth(lw)
        c.line(ML - 2*mm, y, MR + 2*mm, y)

    def vline(x: float, y_top: float, y_bot: float, lw: float = 0.4) -> None:
        c.setLineWidth(lw)
        c.line(x, y_top, x, y_bot)

    def cell(col_idx: int, text: str, row_y: float) -> None:
        """Write text into a table cell."""
        _, x, w, align = COLS[col_idx]
        if align == "center":
            c.drawCentredString(x + w / 2, row_y, text)
        elif align == "right":
            c.drawRightString(x + w - 1*mm, row_y, text)
        else:
            c.drawString(x + 1*mm, row_y, text)

    # ── 1. OUTER BORDER ───────────────────────────────────────────────────────
    # Top edge is fixed; the bottom is drawn in §7 once the content height is
    # known, so the border wraps the content (compact, like the ERP) instead of
    # boxing in the whole empty page.
    border_top = PAGE_H - 8*mm

    # ── 2. HEADER ─────────────────────────────────────────────────────────────
    # NOTE: must clear border_top (PAGE_H - 8mm) by more than the 14pt bold
    # title's ascender height (~3.7mm), or the border line strikes through
    # the company name / "Tax Invoice" text.
    y = PAGE_H - 14*mm

    # Barcode — top-right
    bc_w, bc_h = 36*mm, 13*mm
    barcode_img = ImageReader(_barcode_bytes(invoice.invoice_number))
    c.drawImage(barcode_img, MR - bc_w, y - bc_h,
                width=bc_w, height=bc_h, preserveAspectRatio=False)

    # "Tax Invoice" label immediately left of barcode
    c.setFont("Helvetica-Bold", 9)
    c.drawRightString(MR - bc_w - 3*mm, y, "Tax Invoice")

    # Company name — centred in the non-barcode area
    text_centre = ML + (CW - bc_w) / 2
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(text_centre, y, COMPANY_NAME)

    c.setFont("Helvetica", 7)
    y -= 5*mm;  c.drawCentredString(text_centre, y, COMPANY_ADDR)
    y -= 4*mm;  c.drawCentredString(text_centre, y, COMPANY_LINE2)
    y -= 4*mm;  c.drawCentredString(text_centre, y, COMPANY_LINE3)
    y -= 3.5*mm
    c.setFont("Helvetica-Oblique", 6.5)
    c.drawCentredString(PAGE_W / 2, y, COMPANY_TAX_NOTE)

    y -= 2.5*mm
    hline(y, lw=0.8)

    # ── 3. BUYER / INVOICE META ───────────────────────────────────────────────
    row_top = y
    col_div = ML + CW * 0.50   # mid-page vertical divider

    try:
        inv_date = invoice.business_date.strftime("%d/%m/%Y")
    except Exception:
        inv_date = str(invoice.business_date)

    LBL_W = 30*mm

    # ERP prints the buyer as "NAME-<10-digit phone>".
    _buyer = hotel_name.upper()
    _bp = _buyer_phone(invoice.customer_phone)
    if _bp:
        _buyer = f"{_buyer}-{_bp}"

    for label, value, dy in [
        ("Buyer",           _buyer,          5*mm),
        ("Place Of Supply", PLACE_OF_SUPPLY, 10*mm),
    ]:
        row_y = row_top - dy
        c.setFont("Helvetica-Bold", 8);  c.drawString(ML + 1*mm, row_y, label)
        c.setFont("Helvetica",      8);  c.drawString(ML + LBL_W, row_y, f": {value}")

    for label, value, dy in [
        ("Invoice No.",  invoice.invoice_number, 5*mm),
        ("Invoice Date", inv_date,               10*mm),
    ]:
        row_y = row_top - dy
        c.setFont("Helvetica-Bold", 8);  c.drawString(col_div + 1*mm, row_y, label)
        c.setFont("Helvetica",      8);  c.drawString(col_div + 28*mm, row_y, f": {value}")

    y = row_top - 12*mm
    vline(col_div, row_top, y, lw=0.5)
    hline(y, lw=0.8)

    # ── 4. ITEMS TABLE ────────────────────────────────────────────────────────
    HDR_H = 6.5*mm
    ROW_H = 7.5*mm

    # Header row with grey background
    c.setFillColor(colors.HexColor("#f0f0f0"))
    c.rect(ML - 2*mm, y - HDR_H, CW + 4*mm, HDR_H, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 7)
    for lbl, x, w, align in COLS:
        text_y = y - HDR_H + 1.8*mm
        if align == "center":
            c.drawCentredString(x + w / 2, text_y, lbl)
        elif align == "right":
            c.drawRightString(x + w - 1*mm, text_y, lbl)
        else:
            c.drawString(x + 1*mm, text_y, lbl)

    y -= HDR_H
    hline(y, lw=0.6)
    table_top = y

    total_qty    = Decimal("0")
    total_rate   = Decimal("0")
    total_amount = Decimal("0")

    for idx, item in enumerate(invoice.items, start=1):
        qty    = Decimal(str(item.quantity))
        rate   = Decimal(str(item.rate_used))
        amount = Decimal(str(item.amount))   # stored value — never re-multiply
        total_qty    += qty
        total_rate   += rate
        total_amount += amount

        row_y = y - ROW_H + 2*mm

        if idx % 2 == 0:   # alternate row shading
            c.setFillColor(colors.HexColor("#fafafa"))
            c.rect(ML - 2*mm, y - ROW_H, CW + 4*mm, ROW_H, fill=1, stroke=0)
            c.setFillColor(colors.black)

        item_code, description = _product_info(item.product)
        c.setFont("Helvetica", 7.5)
        for col_idx, text in enumerate([
            str(idx),            # #
            description,         # Description (full ERP name when mapped)
            item_code,           # Itemcode    (real ERP code when mapped)
            _fmt(qty, 3),        # Qty
            _uom(item.unit),     # UOM  (KGS / NOS)
            _fmt(rate, 2),       # Unit Price
            "0.00",              # Discount
            "0.00",              # Discount2
            _fmt(rate, 2),       # Rate  (unit price after discounts)
            _fmt(amount, 3),     # Net Amount
        ]):
            cell(col_idx, text, row_y)

        y -= ROW_H
        c.setLineWidth(0.2)
        c.line(ML - 2*mm, y, MR + 2*mm, y)

    # Total row — mirrors the ERP (sums Qty, Unit Price, Discounts, Net Amount)
    total_row_y = y - ROW_H + 2*mm
    c.setFillColor(colors.HexColor("#f0f0f0"))
    c.rect(ML - 2*mm, y - ROW_H, CW + 4*mm, ROW_H, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 7.5)

    # "Total :" label right-aligned inside Itemcode column
    _, x2, w2, _ = COLS[C_CODE]
    c.drawRightString(x2 + w2 - 1*mm, total_row_y, "Total :")

    cell(C_QTY,   _fmt(total_qty, 3),    total_row_y)   # Qty total
    cell(C_UNIT,  _fmt(total_rate, 3),   total_row_y)   # Unit Price total
    cell(C_DISC,  "0.000",               total_row_y)   # Discount total
    cell(C_DISC2, "0.000",               total_row_y)   # Discount2 total
    cell(C_NET,   _fmt(total_amount, 3), total_row_y)   # Net Amount total

    y -= ROW_H

    # Vertical column dividers across full table height
    c.setLineWidth(0.3)
    for _, x, _, _ in COLS[1:]:
        c.line(x - 0.5*mm, table_top, x - 0.5*mm, y)

    hline(y, lw=0.8)

    # ── 5. AMOUNT-IN-WORDS + CUSTOMER DETAILS (left) + TOTALS (right) ──────────
    section_top = y
    total_val = Decimal(str(invoice.total))

    # Left — amount in words (aligned with the Total row), then customer details
    if address is None:
        address = _lookup_address(invoice.customer_phone)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(ML + 1*mm, section_top - 5*mm, _amount_in_words(total_val))
    c.setFont("Helvetica-Bold", 8)
    c.drawString(ML + 1*mm, section_top - 12*mm, "CUSTOMER DETAILS")
    c.setFont("Helvetica", 8)
    c.drawString(ML + 1*mm, section_top - 17*mm, f"Address : {address}")

    # Right — financial summary (Total / Additional Charge / Round Off / Due)
    c.setFont("Helvetica", 8)
    for label, value, dy in [
        ("Total :",             _fmt(total_val, 3),       5*mm),
        ("Additional Charge :", "0.00",                   10*mm),
        ("Round Off :",         "0.000",                  15*mm),
    ]:
        row_y = section_top - dy
        c.drawString(col_div + 1*mm, row_y, label)
        c.drawRightString(MR, row_y, value)

    # Due Amount — prior outstanding balance (snapshot from the Customer
    # Outstanding sheet) rolled up with the current invoice total.
    prior_outstanding = _lookup_outstanding(invoice.customer_phone)
    due_total = prior_outstanding + total_val
    due_y = section_top - 22*mm
    c.setFont("Helvetica-Bold", 8)
    c.drawString(col_div + 1*mm, due_y, "Due Amount :")
    c.drawRightString(MR, due_y, _fmt(due_total, 3))

    y = section_top - 26*mm
    vline(col_div, section_top, y, lw=0.5)
    hline(y, lw=0.6)

    # ── 6. SIGNATURE (centered in the right half, like the ERP) ───────────────
    right_center = (col_div + MR) / 2
    y -= 4*mm
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(right_center, y, "For, Fluffy Fresh Foods Private Limited")

    line_y = y - 18*mm
    # Embed the scanned authorised signature just above the line, if present.
    if SIGNATURE_PATH.exists():
        try:
            sig_w, sig_h = 30*mm, 13*mm
            sig = ImageReader(str(SIGNATURE_PATH))
            c.drawImage(sig, right_center - sig_w / 2, line_y + 1*mm,
                        width=sig_w, height=sig_h,
                        preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    c.setLineWidth(0.5)
    c.line(right_center - 22*mm, line_y, right_center + 22*mm, line_y)
    c.setFont("Helvetica", 8)
    c.drawCentredString(right_center, line_y - 4*mm, "Authorised Signatory")
    sign_bottom = line_y - 4*mm

    # ── 7. FOOTER + compact outer border ──────────────────────────────────────
    # Footer sits just below the signature; the outer border is closed here so
    # it wraps the content rather than the whole page (no large empty box).
    footer_y   = sign_bottom - 10*mm
    border_bot = footer_y - 4*mm

    c.setLineWidth(1.0)
    c.rect(ML - 2*mm, border_bot, CW + 4*mm, border_top - border_bot)

    hline(footer_y + 4*mm, lw=0.4)
    c.setFont("Helvetica", 7)
    c.drawString(ML, footer_y, "This is a computer generated invoice.")
    c.drawCentredString(PAGE_W / 2, footer_y, "Page 1 of 1")
    c.drawRightString(MR, footer_y, "Next >>")

    c.save()
    return str(out_path.resolve())