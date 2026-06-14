import os
import json
import smtplib
from datetime import datetime, date, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.order import Order
from app.models.inbound_message import InboundMessage
from app.models.customer import Customer
from app.services.notifier import send_whatsapp_template, send_whatsapp_message
from app.services.order_service import get_current_business_date


MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME    = os.getenv("PLANT_NAME", "Fluffy")
IST           = timezone(timedelta(hours=5, minutes=30))

# ── Approved template name ────────────────────────────────────────────────────
TEMPLATE_DAILY_REPORT = "manager_daily_report"

# ── Email config (all optional — WhatsApp still works if unset) ───────────────
REPORT_EMAIL   = os.getenv("REPORT_EMAIL", "")
SMTP_HOST      = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER      = os.getenv("SMTP_USER", "")
SMTP_PASSWORD  = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", f"{PLANT_NAME} OrdeRR")


def _safe_list(value) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def normalize_product(product: str) -> str:
    if "chicken" not in product.lower():
        return f"Chicken {product}"
    return product


def merge_items(items: list) -> list:
    merged = {}
    for item in items:
        product  = normalize_product(item.get("product", "Unknown").strip())
        quantity = item.get("quantity", 0)
        unit     = item.get("unit", "kg").lower()
        key      = f"{product.lower()}||{unit}"
        if key not in merged:
            merged[key] = {"product": product, "quantity": 0, "unit": unit}
        merged[key]["quantity"] += quantity
    return list(merged.values())


def get_todays_customer_notes(db: Session) -> list[dict]:
    today = datetime.now(IST).date()
    note_messages = (
        db.query(InboundMessage)
        .filter(
            func.date(InboundMessage.received_at) == today,
            InboundMessage.processing_status == "NOTE",
            InboundMessage.is_duplicate == False,
        )
        .order_by(InboundMessage.customer_phone, InboundMessage.received_at.asc())
        .all()
    )
    if not note_messages:
        return []
    phones = list({m.customer_phone for m in note_messages})
    customers = db.query(Customer).filter(Customer.phone_number.in_(phones)).all()
    phone_to_name = {c.phone_number: c.restaurant_name or c.phone_number for c in customers}
    notes = []
    for m in note_messages:
        notes.append({
            "restaurant_name": phone_to_name.get(m.customer_phone, m.customer_phone),
            "phone":           m.customer_phone,
            "note":            m.raw_message or "",
            "time":            m.received_at.strftime("%I:%M %p") if m.received_at else "",
        })
    return notes


def generate_daily_report(db: Session) -> dict:
    today     = get_current_business_date()
    today_str = today.strftime("%Y-%m-%d")

    orders = db.query(Order).filter(
        Order.business_date == today_str,
        Order.is_cancelled  == False,
    ).all()

    clear_orders   = [o for o in orders if not o.is_unclear]
    unclear_orders = [o for o in orders if o.is_unclear]

    # ── Product totals (summary section) ─────────────────────────────────────
    product_totals: dict = {}
    for order in clear_orders:
        items = _safe_list(order.parsed_items)
        for item in items:
            product  = normalize_product(item.get("product", "Unknown").strip())
            quantity = item.get("quantity", 0)
            unit     = item.get("unit", "kg").lower()
            key      = f"{product.lower()}||{unit}"
            if key not in product_totals:
                product_totals[key] = {"product": product, "unit": unit, "total_quantity": 0}
            product_totals[key]["total_quantity"] += quantity

    # ── WhatsApp-friendly product summary string ──────────────────────────────
    if not orders:
        product_summary = "No orders received today"
    else:
        lines = []
        for data in product_totals.values():
            qty     = data["total_quantity"]
            qty_str = str(int(qty)) if qty == int(qty) else str(qty)
            lines.append(f"{data['product']} - {qty_str} {data['unit']}")
        if unclear_orders:
            lines.append(f"Unclear: {len(unclear_orders)} (need follow up)")
        product_summary = " | ".join(lines)

    total_items = sum(len(_safe_list(o.parsed_items)) for o in clear_orders)

    return {
        "date_str":        today.strftime("%d %B %Y"),
        "total_orders":    str(len(clear_orders)),
        "total_items":     str(total_items),
        "product_summary": product_summary,
        "_clear_orders":   clear_orders,
        "_unclear_orders": unclear_orders,
        "_product_totals": product_totals,
    }


# ── Printable delivery sheet (HTML) ──────────────────────────────────────────

def _build_print_html(data: dict, notes: list[dict]) -> str:
    date_str       = data["date_str"]
    clear_orders   = data["_clear_orders"]
    unclear_orders = data["_unclear_orders"]
    product_totals = data["_product_totals"]
    generated_at   = datetime.now(IST).strftime("%d %b %Y %I:%M %p IST")

    # ── Product summary rows ──────────────────────────────────────────────────
    summary_rows = ""
    for entry in product_totals.values():
        qty = entry["total_quantity"]
        qty_str = str(int(qty)) if qty == int(qty) else str(qty)
        summary_rows += f"""
                <tr>
                    <td class="product-name">{entry['product']}</td>
                    <td class="qty-ordered">{qty_str} {entry['unit']}</td>
                    <td class="qty-delivered"><div class="write-box"></div></td>
                </tr>"""

    if not summary_rows:
        summary_rows = '<tr><td colspan="3" style="text-align:center;color:#999;padding:16px;">No orders today</td></tr>'

    # ── Per-hotel order rows ──────────────────────────────────────────────────
    hotel_sections = ""
    for idx, order in enumerate(clear_orders, 1):
        items        = _safe_list(order.parsed_items)
        unclear_list = _safe_list(order.unclear_items)
        name         = order.customer_name or order.customer_phone
        delivery     = f" &nbsp;·&nbsp; {order.delivery_time}" if order.delivery_time else ""

        item_rows = ""
        for item in items:
            qty = item.get("quantity", 0)
            qty_str = str(int(qty)) if qty == int(qty) else str(qty)
            item_rows += f"""
                    <tr>
                        <td class="product-name" style="padding-left:24px;">{item.get('product','—')}</td>
                        <td class="qty-ordered">{qty_str} {item.get('unit','kg')}</td>
                        <td class="qty-delivered"><div class="write-box"></div></td>
                    </tr>"""

        for raw in unclear_list:
            item_rows += f"""
                    <tr>
                        <td class="product-name" style="padding-left:24px;color:#e67e22;">⚠️ {raw}</td>
                        <td class="qty-ordered" style="color:#e67e22;">unclear</td>
                        <td class="qty-delivered"><div class="write-box"></div></td>
                    </tr>"""

        hotel_sections += f"""
            <div class="hotel-block">
                <div class="hotel-name">{idx}.&nbsp; {name}{delivery}</div>
                <table class="data-table">
                    <tbody>{item_rows}</tbody>
                </table>
            </div>"""

    if not hotel_sections:
        hotel_sections = '<p style="text-align:center;color:#999;padding:24px 0;">No confirmed orders</p>'

    # ── Unclear section ───────────────────────────────────────────────────────
    unclear_section = ""
    if unclear_orders:
        u_rows = "".join(
            f'<tr><td style="padding:6px 8px;">{o.customer_name or o.customer_phone}</td>'
            f'<td style="padding:6px 8px;font-style:italic;color:#666;">&ldquo;{o.raw_message or ""}&rdquo;</td>'
            f'<td style="padding:6px 8px;color:#999;font-size:11px;">{o.unclear_reason or "—"}</td></tr>'
            for o in unclear_orders
        )
        unclear_section = f"""
            <div class="section" style="margin-top:24px;">
                <div class="section-title" style="color:#c0392b;">&#9888; Unclear Orders — Follow Up ({len(unclear_orders)})</div>
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Customer</th>
                            <th>Message Received</th>
                            <th>Reason</th>
                        </tr>
                    </thead>
                    <tbody>{u_rows}</tbody>
                </table>
            </div>"""

    # ── Notes section ─────────────────────────────────────────────────────────
    notes_section = ""
    if notes:
        n_rows = "".join(
            f'<tr><td style="padding:6px 8px;font-weight:600;">{n["restaurant_name"]}'
            f'{"<span style=color:#999;font-size:11px;> (" + n["time"] + ")</span>" if n["time"] else ""}</td>'
            f'<td style="padding:6px 8px;font-style:italic;">&ldquo;{n["note"]}&rdquo;</td></tr>'
            for n in notes
        )
        notes_section = f"""
            <div class="section" style="margin-top:24px;">
                <div class="section-title">&#128221; Customer Notes</div>
                <table class="data-table">
                    <thead><tr><th>Customer</th><th>Note</th></tr></thead>
                    <tbody>{n_rows}</tbody>
                </table>
            </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Daily Production Report — {PLANT_NAME} — {date_str}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}

  body {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 13px;
    color: #1a1a1a;
    background: #fff;
    padding: 24px 32px;
  }}

  /* ── Header ── */
  .header {{
    border-bottom: 3px solid #1a1a1a;
    padding-bottom: 10px;
    margin-bottom: 20px;
  }}
  .header-plant {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .1em;
    color: #666;
  }}
  .header-title {{
    font-size: 20px;
    font-weight: 700;
    margin: 2px 0;
  }}
  .header-meta {{
    font-size: 12px;
    color: #555;
    margin-top: 4px;
    display: flex;
    justify-content: space-between;
  }}

  /* ── Sections ── */
  .section {{ margin-bottom: 28px; }}
  .section-title {{
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: #333;
    border-bottom: 1.5px solid #333;
    padding-bottom: 5px;
    margin-bottom: 10px;
  }}

  /* ── Tables ── */
  .data-table {{
    width: 100%;
    border-collapse: collapse;
  }}
  .data-table thead tr {{
    background: #f0f0f0;
  }}
  .data-table th {{
    padding: 7px 8px;
    text-align: left;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .05em;
    border-bottom: 1.5px solid #ccc;
  }}
  .data-table td {{
    border-bottom: 1px solid #e8e8e8;
    vertical-align: middle;
  }}
  .data-table tr:last-child td {{ border-bottom: none; }}

  /* ── Column widths ── */
  .product-name   {{ padding: 8px 8px; width: 55%; }}
  .qty-ordered    {{ padding: 8px 8px; width: 20%; font-weight: 600; }}
  .qty-delivered  {{ padding: 6px 8px; width: 25%; }}

  /* ── Write box (blank for pen) ── */
  .write-box {{
    border-bottom: 1.5px solid #333;
    height: 22px;
    width: 90%;
  }}

  /* ── Hotel blocks ── */
  .hotel-block {{ margin-bottom: 18px; }}
  .hotel-name {{
    font-size: 13px;
    font-weight: 700;
    background: #f5f5f5;
    padding: 6px 8px;
    border-left: 4px solid #1a1a1a;
    margin-bottom: 4px;
  }}

  /* ── Footer ── */
  .footer {{
    margin-top: 32px;
    border-top: 1px solid #ccc;
    padding-top: 8px;
    font-size: 10px;
    color: #999;
    display: flex;
    justify-content: space-between;
  }}

  /* ── Signature row ── */
  .sign-row {{
    display: flex;
    gap: 40px;
    margin-top: 40px;
  }}
  .sign-box {{
    flex: 1;
    border-top: 1.5px solid #333;
    padding-top: 6px;
    font-size: 11px;
    color: #555;
    text-align: center;
  }}

  /* ── Print styles ── */
  @media print {{
    body {{ padding: 12px 18px; }}
    @page {{ margin: 12mm 14mm; }}
    .no-print {{ display: none; }}
  }}
</style>
</head>
<body>

<!-- Print button (hidden on print) -->
<div class="no-print" style="text-align:right;margin-bottom:16px;">
  <button onclick="window.print()"
    style="padding:8px 20px;background:#1a1a1a;color:#fff;border:none;
           border-radius:6px;font-size:13px;cursor:pointer;">
    🖨️ Print
  </button>
</div>

<!-- Header -->
<div class="header">
  <div class="header-plant">{PLANT_NAME}</div>
  <div class="header-title">Daily Production Report</div>
  <div class="header-meta">
    <span>{date_str}</span>
    <span>Total Hotels: {len(clear_orders)} &nbsp;|&nbsp; Generated: {generated_at}</span>
  </div>
</div>

<!-- Section 1: Product Summary -->
<div class="section">
  <div class="section-title">Product Summary — Total Quantities</div>
  <table class="data-table">
    <thead>
      <tr>
        <th class="product-name">Product</th>
        <th class="qty-ordered">Ordered Qty</th>
        <th class="qty-delivered">Delivered Qty ✏️</th>
      </tr>
    </thead>
    <tbody>
      {summary_rows}
    </tbody>
  </table>
</div>

<!-- Section 2: Hotel-wise Orders -->
<div class="section">
  <div class="section-title">Hotel-wise Orders</div>
  {hotel_sections}
</div>

{unclear_section}
{notes_section}

<!-- Signature row -->
<div class="sign-row">
  <div class="sign-box">Prepared by</div>
  <div class="sign-box">Checked by</div>
  <div class="sign-box">Accountant</div>
</div>

<div class="footer">
  <span>OrdeRR &middot; {PLANT_NAME}</span>
  <span>{generated_at}</span>
</div>

</body>
</html>"""


def _send_email_report(data: dict, notes: list[dict]):
    """Send the printable Daily Production Report as an HTML email. No-op if env vars not set."""
    if not REPORT_EMAIL:
        print("ℹ️  REPORT_EMAIL not set — skipping email delivery")
        return
    if not SMTP_USER or not SMTP_PASSWORD:
        print("⚠️  SMTP_USER/SMTP_PASSWORD not set — cannot send email report")
        return

    recipients = [r.strip() for r in REPORT_EMAIL.split(",") if r.strip()]
    if not recipients:
        return

    subject   = f"📋 Daily Production Report — {PLANT_NAME} · {data['date_str']}"
    html_body = _build_print_html(data, notes)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{SMTP_FROM_NAME} <{SMTP_USER}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, recipients, msg.as_string())
        print(f"✅ Daily Production Report email sent → {recipients}")
    except Exception as exc:
        print(f"❌ Email report failed: {exc}")


# ── public entry point ────────────────────────────────────────────────────────

def send_daily_report(db: Session):
    """Send daily consolidated report to manager via WhatsApp template.
    Also sends a printable HTML delivery sheet to REPORT_EMAIL if configured."""
    print("\n⏰ Generating Daily Production Report...")

    data = generate_daily_report(db)

    # ── 1. WhatsApp (unchanged) ───────────────────────────────────────────────
    result = send_whatsapp_template(
        MANAGER_PHONE,
        TEMPLATE_DAILY_REPORT,
        [
            PLANT_NAME,
            data["date_str"],
            data["total_orders"],
            data["total_items"],
            data["product_summary"],
        ],
    )

    if result:
        print("✅ Daily report sent via WhatsApp!")
    else:
        print("❌ WhatsApp daily report failed!")

    # ── Customer notes follow-up (unchanged) ──────────────────────────────────
    notes = []
    try:
        notes = get_todays_customer_notes(db)
        if notes:
            lines = [f"📝 *Customer Notes — {PLANT_NAME}*", f"{data['date_str']}", ""]
            for n in notes:
                time_str = f" ({n['time']})" if n['time'] else ""
                lines.append(f"• *{n['restaurant_name']}*{time_str}: {n['note']}")
            notes_msg = "\n".join(lines)
            send_whatsapp_message(MANAGER_PHONE, notes_msg)
            print(f"✅ Customer notes sent ({len(notes)} note(s))")
        else:
            print("ℹ️ No customer notes today — skipping notes message")
    except Exception as e:
        print(f"⚠️ Customer notes follow-up failed: {e}")

    # ── 2. Email — printable delivery sheet (new) ─────────────────────────────
    try:
        _send_email_report(data, notes)
    except Exception as e:
        print(f"⚠️ Email report failed: {e}")