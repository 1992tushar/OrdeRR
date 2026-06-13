"""
invoice_generator.py
Generates a GST Tax Invoice PDF for Fluffy Fresh Foods Private Limited.
Standalone — no other app modules required.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from typing import Optional

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.graphics.barcode import code128


# ── Company constants ────────────────────────────────────────────────────────
COMPANY_NAME       = "Fluffy Fresh Foods Private Limited"
COMPANY_TAGLINE    = "Composition Taxable Person, not eligible to collect tax on supplies"
COMPANY_ADDRESS    = "At Malawadi, Near Kanifnath Mahraj Temple, Talegaon chakan road, Talegaon Dabhade"
COMPANY_GSTIN      = "27AAFCF3001L1ZU"
COMPANY_EMAIL      = "fluffycustomercare@gmail.com"
COMPANY_PHONE      = "9623882123"
COMPANY_CITY       = "Pune"
COMPANY_STATE      = "Maharashtra"
COMPANY_COUNTRY    = "India"
COMPANY_WEBSITE    = "www.fluffymeat.com"

# ── Page geometry ────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4          # 595 × 842 pts
MARGIN         = 20
INNER_LEFT     = MARGIN
INNER_RIGHT    = PAGE_W - MARGIN
INNER_TOP      = PAGE_H - MARGIN
INNER_BOTTOM   = MARGIN
INNER_W        = INNER_RIGHT - INNER_LEFT


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_date(date_str: str) -> str:
    """Convert YYYY-MM-DD → DD/MM/YYYY."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return date_str


def _fmt_num(value: float, decimals: int = 2) -> str:
    return f"{value:,.{decimals}f}"


def _hr(c: canvas.Canvas, y: float, x1: float = None, x2: float = None):
    """Draw a horizontal rule."""
    c.setLineWidth(0.5)
    c.line(x1 or INNER_LEFT, y, x2 or INNER_RIGHT, y)


def _draw_barcode(c: canvas.Canvas, invoice_number: str, x: float, y: float,
                  width: float = 100, height: float = 28):
    """Draw a Code128 barcode using reportlab's built-in barcode engine."""
    bc = code128.Code128(
        invoice_number,
        barWidth=0.9,
        barHeight=height,
        humanReadable=False,
    )
    bc_w = bc.width
    # Scale so it fits the requested width
    scale = width / bc_w if bc_w > 0 else 1.0
    c.saveState()
    c.translate(x, y)
    c.scale(scale, 1.0)
    bc.drawOn(c, 0, 0)
    c.restoreState()


# ── Section drawers ──────────────────────────────────────────────────────────

def _draw_header(c: canvas.Canvas, invoice_data: dict) -> float:
    """
    Draw the company header block.
    Returns the y coordinate of the bottom of the header section.
    """
    inv_number = invoice_data.get("invoice_number", "")

    # ── Outer border (full page minus margins) ────────────────────────────
    c.setLineWidth(1)
    c.rect(INNER_LEFT, INNER_BOTTOM, INNER_W, PAGE_H - 2 * MARGIN)

    # ── "Tax Invoice" box — top right ─────────────────────────────────────
    box_w, box_h = 110, 18
    box_x = INNER_RIGHT - box_w
    box_y = INNER_TOP - box_h
    c.setLineWidth(0.8)
    c.rect(box_x, box_y, box_w, box_h)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(box_x + box_w / 2, box_y + 4, "Tax Invoice")

    # ── Barcode below "Tax Invoice" box ───────────────────────────────────
    bc_y = box_y - 34
    _draw_barcode(c, inv_number, box_x, bc_y, width=box_w, height=28)
    # Small label below barcode
    c.setFont("Helvetica", 6)
    c.drawCentredString(box_x + box_w / 2, bc_y - 7, inv_number)

    # ── Company name (centred, bold, large) ───────────────────────────────
    center_x = INNER_LEFT + INNER_W / 2
    y = INNER_TOP - 18
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(center_x, y, COMPANY_NAME)

    # ── Tagline ───────────────────────────────────────────────────────────
    y -= 14
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(center_x, y, COMPANY_TAGLINE)

    # ── Address ───────────────────────────────────────────────────────────
    y -= 11
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(center_x, y, COMPANY_ADDRESS)

    # ── GSTIN / Email / Contact ───────────────────────────────────────────
    y -= 10
    c.drawCentredString(center_x, y,
        f"GSTIN: {COMPANY_GSTIN}  |  Email: {COMPANY_EMAIL}  |  Contact: {COMPANY_PHONE}")

    # ── City / State / Country / Website ──────────────────────────────────
    y -= 10
    c.drawCentredString(center_x, y,
        f"City: {COMPANY_CITY}  |  State: {COMPANY_STATE}  |  Country: {COMPANY_COUNTRY}"
        f"  |  {COMPANY_WEBSITE}")

    # ── Horizontal rule under header ──────────────────────────────────────
    y -= 7
    _hr(c, y)
    return y


def _draw_buyer_block(c: canvas.Canvas, invoice_data: dict, y_start: float) -> float:
    """Draw buyer info and invoice meta. Returns bottom y."""
    cname  = invoice_data.get("customer_name", "")
    cphone = invoice_data.get("customer_phone", "")
    pos    = invoice_data.get("place_of_supply", "")
    inv_no = invoice_data.get("invoice_number", "")
    inv_dt = _fmt_date(invoice_data.get("invoice_date", ""))

    block_h = 44
    y = y_start - 2

    # Left column
    c.setFont("Helvetica-Bold", 8)
    c.drawString(INNER_LEFT + 4, y - 12, f"Buyer: {cname}  –  {cphone}")
    c.setFont("Helvetica", 8)
    c.drawString(INNER_LEFT + 4, y - 24, f"Place of Supply: {pos}")

    # Right column
    right_x = INNER_LEFT + INNER_W * 0.6
    c.setFont("Helvetica-Bold", 8)
    c.drawString(right_x, y - 12, f"Invoice No.: {inv_no}")
    c.setFont("Helvetica", 8)
    c.drawString(right_x, y - 24, f"Invoice Date: {inv_dt}")

    bottom_y = y - block_h
    _hr(c, bottom_y)
    return bottom_y


def _draw_table(c: canvas.Canvas, line_items: list, y_start: float) -> float:
    """Draw the line-items table. Returns y at bottom of table."""
    # Column definitions: (label, relative_width, alignment)
    COLS = [
        ("#",           0.040, "center"),
        ("Description", 0.200, "left"),
        ("Itemcode",    0.110, "left"),
        ("Qty",         0.065, "right"),
        ("UOM",         0.055, "center"),
        ("Unit Price",  0.090, "right"),
        ("Discount",    0.085, "right"),
        ("Discount2",   0.085, "right"),
        ("Rate",        0.080, "right"),
        ("Net Amount",  0.120, "right"),
    ]

    total_rel = sum(w for _, w, _ in COLS)
    col_widths = [INNER_W * (w / total_rel) for _, w, _ in COLS]
    col_xs = []
    cx = INNER_LEFT
    for w in col_widths:
        col_xs.append(cx)
        cx += w

    ROW_H      = 18
    HEADER_H   = 18
    PAD_LEFT   = 3

    def _cell_x(col_idx: int, align: str, text: str, font: str, size: float) -> float:
        x0 = col_xs[col_idx] + PAD_LEFT
        if align == "right":
            x0 = col_xs[col_idx] + col_widths[col_idx] - PAD_LEFT
        elif align == "center":
            x0 = col_xs[col_idx] + col_widths[col_idx] / 2
        return x0

    def _draw_cell(col_idx: int, label: str, y: float, font: str, size: float, align: str):
        c.setFont(font, size)
        x0 = col_xs[col_idx] + PAD_LEFT
        if align == "right":
            x0 = col_xs[col_idx] + col_widths[col_idx] - PAD_LEFT
            c.drawRightString(x0, y, label)
        elif align == "center":
            x0 = col_xs[col_idx] + col_widths[col_idx] / 2
            c.drawCentredString(x0, y, label)
        else:
            c.drawString(x0, y, label)

    # ── Header row ────────────────────────────────────────────────────────
    y = y_start
    # Shaded header background
    c.setFillColorRGB(0.85, 0.85, 0.85)
    c.rect(INNER_LEFT, y - HEADER_H, INNER_W, HEADER_H, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)

    # Column header labels
    for i, (label, _, align) in enumerate(COLS):
        _draw_cell(i, label, y - HEADER_H + 5, "Helvetica-Bold", 7, align)

    # Header bottom border
    c.setLineWidth(0.5)
    c.line(INNER_LEFT, y - HEADER_H, INNER_RIGHT, y - HEADER_H)
    y -= HEADER_H

    # Vertical lines for header
    for x in col_xs[1:]:
        c.line(x, y_start, x, y)

    # ── Data rows ─────────────────────────────────────────────────────────
    for item in line_items:
        row_top = y
        row_bottom = y - ROW_H

        fields = [
            str(item.get("sr", "")),
            str(item.get("description", "")),
            str(item.get("item_code", "")),
            _fmt_num(item.get("qty", 0), 2),
            str(item.get("uom", "")),
            _fmt_num(item.get("unit_price", 0), 2),
            _fmt_num(item.get("discount", 0), 2),
            _fmt_num(item.get("discount2", 0), 2),
            _fmt_num(item.get("rate", 0), 2),
            _fmt_num(item.get("net_amount", 0), 2),
        ]

        for i, (_, _, align) in enumerate(COLS):
            _draw_cell(i, fields[i], row_bottom + 5, "Helvetica", 7.5, align)

        # Row bottom rule
        c.setLineWidth(0.3)
        c.line(INNER_LEFT, row_bottom, INNER_RIGHT, row_bottom)

        # Vertical lines
        for x in col_xs[1:]:
            c.line(x, row_top, x, row_bottom)

        y = row_bottom

    # Outer verticals for table
    c.setLineWidth(0.5)
    c.line(INNER_LEFT, y_start, INNER_LEFT, y)
    c.line(INNER_RIGHT, y_start, INNER_RIGHT, y)

    return y


def _draw_totals(c: canvas.Canvas, invoice_data: dict, y_start: float) -> float:
    """Draw totals block. Returns bottom y."""
    line_items       = invoice_data.get("line_items", [])
    subtotal         = invoice_data.get("subtotal", 0.0)
    additional       = invoice_data.get("additional_charge", 0.0)
    round_off        = invoice_data.get("round_off", 0.0)
    total            = invoice_data.get("total_amount", 0.0)
    due              = invoice_data.get("due_amount", 0.0)
    amount_in_words  = invoice_data.get("amount_in_words", "")

    total_qty = sum(item.get("qty", 0) for item in line_items)

    y = y_start - 4
    right_col_x  = INNER_LEFT + INNER_W * 0.55
    right_label  = right_col_x + 4
    right_value  = INNER_RIGHT - 4

    def _totals_row(label: str, value: str, bold: bool = False):
        nonlocal y
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, 8)
        c.drawString(right_label, y, label)
        c.drawRightString(right_value, y, value)
        y -= 13

    # Total qty + total net amount line
    c.setFont("Helvetica-Bold", 8)
    c.drawString(INNER_LEFT + 4, y, f"Total Qty: {_fmt_num(total_qty, 2)}")
    _totals_row("Total Net Amount:", _fmt_num(subtotal, 2), bold=True)

    # Amount in words (left, italic)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(INNER_LEFT + 4, y + 2, f"Amount in words: {amount_in_words}")

    _totals_row("Total:", _fmt_num(total, 2), bold=True)
    _totals_row(f"Additional Charge:", _fmt_num(additional, 2))
    _totals_row(f"Round Off:", _fmt_num(round_off, 3))

    # Separator line
    c.setLineWidth(0.5)
    c.line(right_col_x, y + 10, INNER_RIGHT, y + 10)

    _totals_row(f"BY BANK:  {_fmt_num(total, 2)}", f"Due Amount: {_fmt_num(due, 2)}", bold=True)

    return y


def _draw_footer(c: canvas.Canvas, invoice_data: dict, y_bottom: float):
    """Draw the footer block at the bottom of the page."""
    cname  = invoice_data.get("customer_name", "")
    cphone = invoice_data.get("customer_phone", "")

    footer_top    = y_bottom + 30
    footer_mid_y  = INNER_BOTTOM + 22
    center_y      = INNER_BOTTOM + 10

    # Horizontal rule above footer
    _hr(c, footer_top)

    # Left: CUSTOMER DETAILS
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(INNER_LEFT + 4, footer_top - 10, "CUSTOMER DETAILS")
    c.setFont("Helvetica", 7.5)
    c.drawString(INNER_LEFT + 4, footer_top - 20, cname)
    c.drawString(INNER_LEFT + 4, footer_top - 30, cphone)

    # Right: Authorised signatory
    sig_x = INNER_LEFT + INNER_W * 0.65
    c.setFont("Helvetica", 7.5)
    c.drawString(sig_x, footer_top - 10, "For, Fluffy Fresh Foods Private Limited")
    c.drawString(sig_x, footer_top - 30, "Authorised Signatory")

    # Bottom center: disclaimer + page
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(INNER_LEFT + INNER_W / 2, center_y + 6,
                        "This is computer generated Invoice.")
    c.setFont("Helvetica", 7)
    c.drawCentredString(INNER_LEFT + INNER_W / 2, center_y - 2, "Page 1 of 1")


# ── Public API ───────────────────────────────────────────────────────────────

def generate_invoice_pdf(invoice_data: dict, output_path: str) -> None:
    """
    Generate a GST Tax Invoice PDF for Fluffy Fresh Foods Private Limited.

    Parameters
    ----------
    invoice_data : dict
        Invoice payload (see module docstring for shape).
    output_path : str
        Filesystem path where the PDF should be written.
    """
    c = canvas.Canvas(output_path, pagesize=A4)
    c.setTitle(f"Tax Invoice – {invoice_data.get('invoice_number', '')}")

    # ── Header ────────────────────────────────────────────────────────────
    header_bottom = _draw_header(c, invoice_data)

    # ── Buyer / meta block ────────────────────────────────────────────────
    buyer_bottom = _draw_buyer_block(c, invoice_data, header_bottom)

    # ── Line items table ──────────────────────────────────────────────────
    table_bottom = _draw_table(c, invoice_data.get("line_items", []), buyer_bottom)

    # ── Totals ────────────────────────────────────────────────────────────
    totals_bottom = _draw_totals(c, invoice_data, table_bottom)

    # ── Footer (pinned to bottom 30pt) ────────────────────────────────────
    _draw_footer(c, invoice_data, INNER_BOTTOM)

    c.save()


# ── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_data = {
        "invoice_number":   "INV42",
        "invoice_date":     "2026-06-12",
        "customer_name":    "FAIZ KHATIK",
        "customer_phone":   "919876543210",
        "place_of_supply":  "Maharashtra",
        "line_items": [
            {
                "sr":          1,
                "description": "Chicken Feet",
                "item_code":   "CH1024568",
                "qty":         10.0,
                "uom":         "KGS",
                "unit_price":  45.00,
                "discount":    0.00,
                "discount2":   0.00,
                "rate":        45.00,
                "net_amount":  450.00,
            },
            {
                "sr":          2,
                "description": "Chicken Breast Boneless",
                "item_code":   "CH2031190",
                "qty":         5.0,
                "uom":         "KGS",
                "unit_price":  220.00,
                "discount":    0.00,
                "discount2":   0.00,
                "rate":        220.00,
                "net_amount":  1100.00,
            },
        ],
        "subtotal":          1550.00,
        "additional_charge": 0.00,
        "round_off":         0.00,
        "total_amount":      1550.00,
        "due_amount":        1550.00,
        "amount_in_words":   "Rupees One Thousand Five Hundred and Fifty Only",
    }

    out = "test_invoice.pdf"
    generate_invoice_pdf(test_data, out)
    print(f"Generated: {out}")