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
    from app.models.invoice import Invoice

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

# ── Page geometry ─────────────────────────────────────────────────────────────
# A4 = 595.28 × 841.89 pts.  18 mm margins → 174 mm content width.
PAGE_W, PAGE_H = A4
ML = 18 * mm
MR = PAGE_W - 18 * mm
CW = MR - ML   # exactly 174 mm

# ── 9-Column table layout (widths in mm, total = 174) ─────────────────────────
# Format: (header, x_offset_mm, width_mm, align)
_COL_DEFS = [
    ("#",           0,   5,  "center"),
    ("Description", 5,  51,  "left"),
    ("Itemcode",   56,  22,  "center"),
    ("Qty",        78,  14,  "right"),
    ("UOM",        92,  10,  "center"),
    ("Unit Price", 102, 20,  "right"),
    ("Discount",   122, 15,  "right"),
    ("Discount2",  137, 16,  "right"),
    ("Net Amount", 153, 21,  "right"),
]
# Convert offsets to absolute x positions in points
COLS = [(lbl, ML + x*mm, w*mm, align) for lbl, x, w, align in _COL_DEFS]


def _fmt(value, decimals: int = 3) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    return f"{v:,.{decimals}f}"


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


def generate_invoice_pdf(invoice: "Invoice", hotel_name: str) -> str:
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

    c = canvas.Canvas(str(out_path), pagesize=A4)

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
    border_bot = 8*mm
    border_top = PAGE_H - 8*mm
    c.setLineWidth(1.0)
    c.rect(ML - 2*mm, border_bot, CW + 4*mm, border_top - border_bot)

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

    for label, value, dy in [
        ("Buyer",           hotel_name.upper(), 5*mm),
        ("Place Of Supply", PLACE_OF_SUPPLY,   10*mm),
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
    total_amount = Decimal("0")

    for idx, item in enumerate(invoice.items, start=1):
        qty    = Decimal(str(item.quantity))
        rate   = Decimal(str(item.rate_used))
        amount = Decimal(str(item.amount))   # stored value — never re-multiply
        total_qty    += qty
        total_amount += amount

        row_y = y - ROW_H + 2*mm

        if idx % 2 == 0:   # alternate row shading
            c.setFillColor(colors.HexColor("#fafafa"))
            c.rect(ML - 2*mm, y - ROW_H, CW + 4*mm, ROW_H, fill=1, stroke=0)
            c.setFillColor(colors.black)

        c.setFont("Helvetica", 7.5)
        for col_idx, text in enumerate([
            str(idx),            # #
            item.product,        # Description
            ITEM_CODE,           # Itemcode
            _fmt(qty, 3),        # Qty
            item.unit.upper(),   # UOM
            _fmt(rate, 2),       # Unit Price
            "0.00",              # Discount
            "0.00",              # Discount2
            _fmt(amount, 3),     # Net Amount  ← now in correct last column
        ]):
            cell(col_idx, text, row_y)

        y -= ROW_H
        c.setLineWidth(0.2)
        c.line(ML - 2*mm, y, MR + 2*mm, y)

    # Total row
    total_row_y = y - ROW_H + 2*mm
    c.setFillColor(colors.HexColor("#f0f0f0"))
    c.rect(ML - 2*mm, y - ROW_H, CW + 4*mm, ROW_H, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 7.5)

    # "Total :" label right-aligned inside Itemcode column
    _, x2, w2, _ = COLS[2]
    c.drawRightString(x2 + w2 - 1*mm, total_row_y, "Total :")

    cell(3, _fmt(total_qty, 3),    total_row_y)   # Qty total
    cell(6, "0.000",               total_row_y)   # Discount total
    cell(7, "0.000",               total_row_y)   # Discount2 total
    cell(8, _fmt(total_amount, 3), total_row_y)   # Net Amount total

    y -= ROW_H

    # Vertical column dividers across full table height
    c.setLineWidth(0.3)
    for _, x, _, _ in COLS[1:]:
        c.line(x - 0.5*mm, table_top, x - 0.5*mm, y)

    hline(y, lw=0.8)

    # ── 5. CUSTOMER DETAILS (left) + TOTALS (right) ───────────────────────────
    section_top = y
    total_val = Decimal(str(invoice.total))

    # Right — financial summary
    c.setFont("Helvetica", 8)
    for label, value, dy in [
        ("Total :",             _fmt(total_val, 3), 5*mm),
        ("Additional Charge :", "0.00",             10*mm),
        ("Round Off :",         "0.000",            15*mm),
    ]:
        row_y = section_top - dy
        c.drawString(col_div + 1*mm, row_y, label)
        c.drawRightString(MR, row_y, value)

    # Left — customer info (name + phone separately)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(ML + 1*mm, section_top - 5*mm, "CUSTOMER DETAILS")
    c.setFont("Helvetica", 8)
    c.drawString(ML + 1*mm, section_top - 10*mm, f"Name  : {hotel_name}")
    c.drawString(ML + 1*mm, section_top - 15*mm, f"Phone : {invoice.customer_phone}")

    y = section_top - 18*mm
    vline(col_div, section_top, y, lw=0.5)
    hline(y, lw=0.6)

    # ── 6. SIGNATURE ──────────────────────────────────────────────────────────
    y -= 3*mm
    c.setFont("Helvetica", 8)
    c.drawRightString(MR, y, "For, Fluffy Fresh Foods Private Limited")
    y -= 18*mm
    c.setLineWidth(0.5)
    c.line(MR - 45*mm, y, MR, y)
    c.setFont("Helvetica", 8)
    c.drawRightString(MR, y - 4*mm, "Authorised Signatory")

    # ── 7. FOOTER ─────────────────────────────────────────────────────────────
    footer_y = border_bot + 3*mm
    hline(footer_y + 4*mm, lw=0.4)
    c.setFont("Helvetica", 7)
    c.drawString(ML, footer_y, "This is a computer generated invoice.")
    c.drawCentredString(PAGE_W / 2, footer_y, "Page 1 of 1")
    c.drawRightString(MR, footer_y, "Next >>")

    c.save()
    return str(out_path.resolve())