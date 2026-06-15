"""
invoice_generator.py
Generates a GST Tax Invoice PDF for Fluffy Fresh Foods Private Limited.
Standalone — no other app modules required.
"""

from __future__ import annotations

import os
from datetime import datetime

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.graphics.barcode import code128


# ── Company constants ────────────────────────────────────────────────────────
COMPANY_NAME    = "Fluffy Fresh Foods Private Limited"
COMPANY_TAGLINE = "Composition Taxable Person, not eligible to collect tax on supplies"
COMPANY_ADDRESS = "At Malawadi, Near Kanifnath Mahraj Temple, Talegaon chakan road, Talegaon Dabhade"
COMPANY_GSTIN   = "27AAFCF3001L1ZU"
COMPANY_EMAIL   = "fluffycustomercare@gmail.com"
COMPANY_PHONE   = "9623882123"
COMPANY_CITY    = "Pune"
COMPANY_STATE   = "Maharashtra"
COMPANY_COUNTRY = "India"
COMPANY_WEBSITE = "www.fluffymeat.com"

# ── Page geometry ────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4
MARGIN       = 20
INNER_LEFT   = MARGIN
INNER_RIGHT  = PAGE_W - MARGIN
INNER_TOP    = PAGE_H - MARGIN
INNER_BOTTOM = MARGIN
INNER_W      = INNER_RIGHT - INNER_LEFT


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_date(date_str: str) -> str:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return date_str


def _fmt_num(value: float, decimals: int = 2) -> str:
    return f"{value:,.{decimals}f}"


def _hr(c: canvas.Canvas, y: float, x1: float = None, x2: float = None):
    c.setLineWidth(0.5)
    c.line(x1 or INNER_LEFT, y, x2 or INNER_RIGHT, y)


def _draw_barcode(c: canvas.Canvas, invoice_number: str, x: float, y: float,
                  width: float = 100, height: float = 28):
    bc = code128.Code128(
        invoice_number,
        barWidth=0.9,
        barHeight=height,
        humanReadable=False,
    )
    bc_w = bc.width
    scale = width / bc_w if bc_w > 0 else 1.0
    c.saveState()
    c.translate(x, y)
    c.scale(scale, 1.0)
    bc.drawOn(c, 0, 0)
    c.restoreState()


# ── Section drawers ──────────────────────────────────────────────────────────

def _draw_header(c: canvas.Canvas, invoice_data: dict) -> float:
    inv_number = invoice_data.get("invoice_number", "")

    # Outer border
    c.setLineWidth(1)
    c.rect(INNER_LEFT, INNER_BOTTOM, INNER_W, PAGE_H - 2 * MARGIN)

    # "Tax Invoice" box — top right
    box_w, box_h = 110, 18
    box_x = INNER_RIGHT - box_w
    box_y = INNER_TOP - box_h
    c.setLineWidth(0.8)
    c.rect(box_x, box_y, box_w, box_h)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(box_x + box_w / 2, box_y + 4, "Tax Invoice")

    # Barcode below box
    bc_y = box_y - 36
    _draw_barcode(c, inv_number, box_x, bc_y, width=box_w, height=28)
    c.setFont("Helvetica", 6)
    c.drawCentredString(box_x + box_w / 2, bc_y - 8, inv_number)

    # Company name — centred, but shifted left to avoid barcode overlap
    center_x = INNER_LEFT + (INNER_W - box_w) / 2
    y = INNER_TOP - 16
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(center_x, y, COMPANY_NAME)

    y -= 13
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(center_x, y, COMPANY_TAGLINE)

    y -= 11
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(center_x, y, COMPANY_ADDRESS)

    y -= 10
    c.drawCentredString(center_x, y,
        f"GSTIN: {COMPANY_GSTIN}  |  Email: {COMPANY_EMAIL}  |  Contact: {COMPANY_PHONE}")

    y -= 10
    c.drawCentredString(center_x, y,
        f"City: {COMPANY_CITY}  |  State: {COMPANY_STATE}  |  Country: {COMPANY_COUNTRY}"
        f"  |  {COMPANY_WEBSITE}")

    y -= 8
    _hr(c, y)
    return y


def _draw_buyer_block(c: canvas.Canvas, invoice_data: dict, y_start: float) -> float:
    """Draw buyer info and invoice meta. Returns bottom y."""
    cname  = invoice_data.get("customer_name", "")
    cphone = invoice_data.get("customer_phone", "")
    pos    = invoice_data.get("place_of_supply", "")
    inv_no = invoice_data.get("invoice_number", "")
    inv_dt = _fmt_date(invoice_data.get("invoice_date", ""))

    ROW_H   = 14   # height per line
    PAD_TOP = 6    # padding below the HR

    y = y_start - PAD_TOP

    # Left column
    c.setFont("Helvetica-Bold", 8)
    c.drawString(INNER_LEFT + 6, y - ROW_H, f"Buyer: {cname}  –  {cphone}")
    if pos:
        c.setFont("Helvetica", 8)
        c.drawString(INNER_LEFT + 6, y - ROW_H * 2, f"Place of Supply: {pos}")

    # Right column — right-aligned labels with fixed value start
    right_label_x = INNER_LEFT + INNER_W * 0.62
    right_value_x = INNER_RIGHT - 6
    c.setFont("Helvetica-Bold", 8)
    c.drawString(right_label_x, y - ROW_H, "Invoice No.:")
    c.setFont("Helvetica", 8)
    c.drawRightString(right_value_x, y - ROW_H, inv_no)

    c.setFont("Helvetica-Bold", 8)
    c.drawString(right_label_x, y - ROW_H * 2, "Invoice Date:")
    c.setFont("Helvetica", 8)
    c.drawRightString(right_value_x, y - ROW_H * 2, inv_dt)

    block_rows = 2 if pos else 1
    bottom_y   = y - ROW_H * block_rows - PAD_TOP
    _hr(c, bottom_y)
    return bottom_y


def _draw_table(c: canvas.Canvas, line_items: list, y_start: float) -> float:
    """Draw the line-items table. Returns y at bottom of table."""
    # Column definitions: (label, relative_width, alignment)
    # Itemcode removed — column width redistributed to Description and Net Amount
    COLS = [
        ("#",          0.040, "center"),
        ("Description",0.310, "left"),
        ("Qty",        0.080, "right"),
        ("UOM",        0.070, "center"),
        ("Unit Price", 0.130, "right"),
        ("Discount",   0.100, "right"),
        ("Rate",       0.100, "right"),
        ("Net Amount", 0.170, "right"),
    ]

    total_rel  = sum(w for _, w, _ in COLS)
    col_widths = [INNER_W * (w / total_rel) for _, w, _ in COLS]
    col_xs     = []
    cx = INNER_LEFT
    for w in col_widths:
        col_xs.append(cx)
        cx += w

    ROW_H    = 18
    HEADER_H = 18
    PAD      = 4

    def _draw_cell(col_idx: int, label: str, y: float, font: str,
                   size: float, align: str):
        c.setFont(font, size)
        if align == "right":
            x = col_xs[col_idx] + col_widths[col_idx] - PAD
            c.drawRightString(x, y, label)
        elif align == "center":
            x = col_xs[col_idx] + col_widths[col_idx] / 2
            c.drawCentredString(x, y, label)
        else:
            x = col_xs[col_idx] + PAD
            c.drawString(x, y, label)

    # ── Header row ────────────────────────────────────────────────────────
    y = y_start
    c.setFillColorRGB(0.88, 0.88, 0.88)
    c.rect(INNER_LEFT, y - HEADER_H, INNER_W, HEADER_H, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)

    for i, (label, _, align) in enumerate(COLS):
        _draw_cell(i, label, y - HEADER_H + 6, "Helvetica-Bold", 7.5, align)

    c.setLineWidth(0.5)
    c.line(INNER_LEFT, y - HEADER_H, INNER_RIGHT, y - HEADER_H)
    y -= HEADER_H

    for x in col_xs[1:]:
        c.line(x, y_start, x, y)

    # ── Data rows ─────────────────────────────────────────────────────────
    for item in line_items:
        row_top    = y
        row_bottom = y - ROW_H

        # Map billing_service keys → table column order (no item_code column)
        fields = [
            str(item.get("sr", "")),
            str(item.get("description", item.get("product", ""))),
            _fmt_num(item.get("qty",        item.get("quantity", 0)), 2),
            str(item.get("uom",             item.get("unit", ""))),
            _fmt_num(item.get("unit_price", 0), 2),
            _fmt_num(item.get("discount",   0), 2),
            _fmt_num(item.get("rate",       item.get("unit_price", 0)), 2),
            _fmt_num(item.get("net_amount", 0), 2),
        ]

        for i, (_, _, align) in enumerate(COLS):
            _draw_cell(i, fields[i], row_bottom + 6, "Helvetica", 8, align)

        c.setLineWidth(0.3)
        c.line(INNER_LEFT, row_bottom, INNER_RIGHT, row_bottom)

        for x in col_xs[1:]:
            c.line(x, row_top, x, row_bottom)

        y = row_bottom

    # Outer verticals
    c.setLineWidth(0.5)
    c.line(INNER_LEFT,  y_start, INNER_LEFT,  y)
    c.line(INNER_RIGHT, y_start, INNER_RIGHT, y)

    return y


def _draw_totals(c: canvas.Canvas, invoice_data: dict, y_start: float) -> float:
    """Draw totals block. Returns bottom y."""
    line_items      = invoice_data.get("line_items", [])
    subtotal        = invoice_data.get("subtotal",        0.0)
    additional      = invoice_data.get("additional_charge", 0.0)
    round_off       = invoice_data.get("round_off",       0.0)
    total           = invoice_data.get("total_amount",    0.0)
    due             = invoice_data.get("due_amount",      0.0)
    words           = invoice_data.get("amount_in_words", "")

    total_qty = sum(
        item.get("qty", item.get("quantity", 0)) for item in line_items
    )

    # Divider between left summary and right totals
    split_x     = INNER_LEFT + INNER_W * 0.52
    label_x     = split_x + 6
    value_x     = INNER_RIGHT - 6
    ROW_H       = 15
    PAD_TOP     = 6

    y = y_start - PAD_TOP

    # ── Left side: total qty + amount in words ────────────────────────────
    c.setFont("Helvetica-Bold", 8)
    c.drawString(INNER_LEFT + 6, y, f"Total Qty: {_fmt_num(total_qty, 2)}")

    # Amount in words sits below total qty with a gap
    y_words = y - ROW_H
    c.setFont("Helvetica-Oblique", 7.5)
    c.drawString(INNER_LEFT + 6, y_words, f"Amount in words:")
    c.drawString(INNER_LEFT + 6, y_words - 11, words)

    # ── Right side: totals rows ───────────────────────────────────────────
    # Vertical divider
    c.setLineWidth(0.4)
    c.line(split_x, y_start, split_x, y_start - ROW_H * 5 - PAD_TOP * 2)

    y_right = y  # start right column at same y as left

    def _row(label: str, value: str, bold: bool = False):
        nonlocal y_right
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, 8)
        c.drawString(label_x, y_right, label)
        c.drawRightString(value_x, y_right, value)
        y_right -= ROW_H

    _row("Total Net Amount:", _fmt_num(subtotal,   2), bold=True)
    _row("Total:",            _fmt_num(total,      2), bold=True)
    _row("Additional Charge:", _fmt_num(additional, 2))
    _row("Round Off:",        _fmt_num(round_off,  3))

    # Separator above Due Amount
    c.setLineWidth(0.5)
    c.line(split_x, y_right + ROW_H - 2, INNER_RIGHT, y_right + ROW_H - 2)

    _row(f"BY BANK:  {_fmt_num(total, 2)}",
         f"Due Amount: {_fmt_num(due, 2)}", bold=True)

    # Bottom border of totals block
    bottom_y = min(y_words - 11 - 6, y_right)  # whichever is lower
    _hr(c, bottom_y)

    return bottom_y


def _draw_footer(c: canvas.Canvas, invoice_data: dict, y_bottom: float):
    """Draw the footer block at the bottom of the page."""
    cname  = invoice_data.get("customer_name", "")
    cphone = invoice_data.get("customer_phone", "")

    footer_top = y_bottom + 36
    center_y   = y_bottom + 8

    _hr(c, footer_top)

    # Left: customer details
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(INNER_LEFT + 6, footer_top - 10, "CUSTOMER DETAILS")
    c.setFont("Helvetica", 7.5)
    c.drawString(INNER_LEFT + 6, footer_top - 21, cname)
    c.drawString(INNER_LEFT + 6, footer_top - 31, cphone)

    # Right: authorised signatory
    sig_x = INNER_LEFT + INNER_W * 0.65
    c.setFont("Helvetica", 7.5)
    c.drawString(sig_x, footer_top - 10, "For, Fluffy Fresh Foods Private Limited")
    c.drawString(sig_x, footer_top - 31, "Authorised Signatory")

    # Bottom centre
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(INNER_LEFT + INNER_W / 2, center_y + 5,
                        "This is a computer generated Invoice.")
    c.setFont("Helvetica", 7)
    c.drawCentredString(INNER_LEFT + INNER_W / 2, center_y - 3, "Page 1 of 1")


# ── Public API ───────────────────────────────────────────────────────────────

def generate_invoice_pdf(invoice_data: dict, output_path: str) -> None:
    """
    Generate a GST Tax Invoice PDF.

    Parameters
    ----------
    invoice_data : dict
    output_path  : str  — filesystem path where PDF is written
    """
    c = canvas.Canvas(output_path, pagesize=A4, compress=0)
    c.setTitle(f"Tax Invoice – {invoice_data.get('invoice_number', '')}")

    header_bottom  = _draw_header(c, invoice_data)
    buyer_bottom   = _draw_buyer_block(c, invoice_data, header_bottom)
    table_bottom   = _draw_table(c, invoice_data.get("line_items", []), buyer_bottom)
    _draw_totals(c, invoice_data, table_bottom)
    _draw_footer(c, invoice_data, INNER_BOTTOM)

    c.save()


# ── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_data = {
        "invoice_number":    "INV42",
        "invoice_date":      "2026-06-12",
        "customer_name":     "FAIZ KHATIK",
        "customer_phone":    "919876543210",
        "place_of_supply":   "Maharashtra",
        "line_items": [
            {
                "sr": 1, "description": "Chicken Feet",
                "qty": 10.0, "uom": "KGS",
                "unit_price": 45.00, "discount": 0.00,
                "rate": 45.00, "net_amount": 450.00,
            },
            {
                "sr": 2, "description": "Chicken Breast Boneless",
                "qty": 5.0, "uom": "KGS",
                "unit_price": 220.00, "discount": 0.00,
                "rate": 220.00, "net_amount": 1100.00,
            },
        ],
        "subtotal":          1550.00,
        "additional_charge": 0.00,
        "round_off":         0.00,
        "total_amount":      1550.00,
        "due_amount":        1550.00,
        "amount_in_words":   "Rupees One Thousand Five Hundred and Fifty Only",
    }

    generate_invoice_pdf(test_data, "test_invoice.pdf")
    print("Generated: test_invoice.pdf")