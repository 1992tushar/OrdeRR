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
REPORT_EMAIL   = os.getenv("REPORT_EMAIL", "")        # comma-separated recipients
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
        if isinstance(parsed, str):      # double-encoded
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
    """
    Fetch all inbound messages marked as NOTE for today.
    Returns list of dicts: {restaurant_name, phone, note, time}
    """
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
    """
    Generate consolidated daily order report.
    Always returns a dict — sends 'no orders' message when count is zero.
    """
    today = get_current_business_date()
    today_str = today.strftime("%Y-%m-%d")

    orders = db.query(Order).filter(
        Order.business_date == today_str,
        Order.is_cancelled == False,
    ).all()

    clear_orders   = [o for o in orders if not o.is_unclear]
    unclear_orders = [o for o in orders if o.is_unclear]

    # Aggregate product totals
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

    # Product summary string — pipe-separated, no newlines (Meta template requirement)
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

    # Total items count
    total_items = sum(len(_safe_list(o.parsed_items)) for o in clear_orders)

    return {
        "date_str":        today.strftime("%d %B %Y"),
        "total_orders":    str(len(clear_orders)),
        "total_items":     str(total_items),
        "product_summary": product_summary,
        # extra fields used by email renderer (not sent to WhatsApp template)
        "_clear_orders":   clear_orders,
        "_unclear_orders": unclear_orders,
        "_product_totals": product_totals,
    }


# ── Email helpers (new) ───────────────────────────────────────────────────────

def _build_email_html(data: dict, notes: list[dict]) -> str:
    """Build a readable HTML email for the Daily Production Report."""
    date_str       = data["date_str"]
    order_count    = data["total_orders"]
    total_items    = data["total_items"]
    clear_orders   = data["_clear_orders"]
    unclear_orders = data["_unclear_orders"]
    product_totals = data["_product_totals"]
    unclear_count  = len(unclear_orders)

    # ── Product totals rows ───────────────────────────────────────────────────
    product_rows = ""
    for entry in product_totals.values():
        qty = entry["total_quantity"]
        qty_str = str(int(qty)) if qty == int(qty) else str(qty)
        product_rows += (
            f'<tr>'
            f'<td style="padding:10px 16px;border-bottom:1px solid #f0f0f0;font-size:14px;">{entry["product"]}</td>'
            f'<td style="padding:10px 16px;border-bottom:1px solid #f0f0f0;font-size:14px;font-weight:700;'
            f'color:#075e54;text-align:right;">{qty_str} {entry["unit"]}</td>'
            f'</tr>'
        )
    if not product_rows:
        product_rows = '<tr><td colspan="2" style="padding:20px;text-align:center;color:#999;">No orders today</td></tr>'

    # ── Order detail rows ─────────────────────────────────────────────────────
    order_rows = ""
    for order in clear_orders:
        items        = _safe_list(order.parsed_items)
        unclear_list = _safe_list(order.unclear_items)
        name         = order.customer_name or order.customer_phone
        items_text   = "<br>".join(
            f"{i.get('product')} — "
            f"{int(i['quantity']) if i['quantity'] == int(i['quantity']) else i['quantity']} "
            f"{i.get('unit', 'kg')}"
            for i in items
        ) or "—"
        unclear_extra = (
            f'<br><span style="color:#e67e22;font-size:12px;">⚠️ Unclear: {", ".join(unclear_list)}</span>'
            if unclear_list else ""
        )
        delivery = order.delivery_time or "—"
        order_rows += (
            f'<tr>'
            f'<td style="padding:10px 16px;border-bottom:1px solid #f0f0f0;font-size:13px;font-weight:600;">{name}</td>'
            f'<td style="padding:10px 16px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#444;">{items_text}{unclear_extra}</td>'
            f'<td style="padding:10px 16px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#888;white-space:nowrap;">{delivery}</td>'
            f'</tr>'
        )
    if not order_rows:
        order_rows = '<tr><td colspan="3" style="padding:20px;text-align:center;color:#999;">No confirmed orders</td></tr>'

    # ── Unclear section ───────────────────────────────────────────────────────
    unclear_section = ""
    if unclear_orders:
        u_rows = "".join(
            f'<tr>'
            f'<td style="padding:8px 16px;border-bottom:1px solid #fff3e0;font-size:13px;">{o.customer_name or o.customer_phone}</td>'
            f'<td style="padding:8px 16px;border-bottom:1px solid #fff3e0;font-size:13px;color:#e67e22;font-style:italic;">&ldquo;{o.raw_message or ""}&rdquo;</td>'
            f'<td style="padding:8px 16px;border-bottom:1px solid #fff3e0;font-size:12px;color:#999;">{o.unclear_reason or "—"}</td>'
            f'</tr>'
            for o in unclear_orders
        )
        unclear_section = (
            f'<h2 style="font-size:15px;color:#e67e22;margin:32px 0 12px;font-weight:700;">⚠️ Unclear Orders ({unclear_count})</h2>'
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#fffbf5;border-radius:10px;border:1px solid #ffe0b2;border-collapse:collapse;">'
            f'<thead><tr style="background:#fff3e0;">'
            f'<th style="padding:10px 16px;text-align:left;font-size:12px;color:#e65100;border-bottom:1px solid #ffe0b2;">Customer</th>'
            f'<th style="padding:10px 16px;text-align:left;font-size:12px;color:#e65100;border-bottom:1px solid #ffe0b2;">Raw Message</th>'
            f'<th style="padding:10px 16px;text-align:left;font-size:12px;color:#e65100;border-bottom:1px solid #ffe0b2;">Reason</th>'
            f'</tr></thead>'
            f'<tbody>{u_rows}</tbody></table>'
        )

    # ── Notes section ─────────────────────────────────────────────────────────
    notes_section = ""
    if notes:
        n_rows = "".join(
            f'<tr>'
            f'<td style="padding:8px 16px;border-bottom:1px solid #f0f0f0;font-size:13px;font-weight:600;">'
            f'{n["restaurant_name"]}'
            f'{"<span style=color:#aaa;font-size:11px;> (" + n["time"] + ")</span>" if n["time"] else ""}'
            f'</td>'
            f'<td style="padding:8px 16px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#555;font-style:italic;">&ldquo;{n["note"]}&rdquo;</td>'
            f'</tr>'
            for n in notes
        )
        notes_section = (
            f'<h2 style="font-size:15px;color:#555;margin:32px 0 12px;font-weight:700;">📝 Customer Notes ({len(notes)})</h2>'
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;border:1px solid #e0e0e0;border-collapse:collapse;">'
            f'<thead><tr style="background:#f5f5f5;">'
            f'<th style="padding:10px 16px;text-align:left;font-size:12px;color:#555;border-bottom:1px solid #e0e0e0;">Customer</th>'
            f'<th style="padding:10px 16px;text-align:left;font-size:12px;color:#555;border-bottom:1px solid #e0e0e0;">Note</th>'
            f'</tr></thead>'
            f'<tbody>{n_rows}</tbody></table>'
        )

    unclear_badge = (
        f'<span style="background:#fff3e0;color:#e65100;border-radius:99px;padding:3px 10px;font-size:12px;font-weight:700;margin-left:8px;">⚠️ {unclear_count} unclear</span>'
        if unclear_count else ""
    )
    generated_at = datetime.now(IST).strftime("%d %b %Y %I:%M %p IST")

    return (
        "<!DOCTYPE html><html><head>"
        '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "</head>"
        '<body style="margin:0;padding:0;background:#f0f4f2;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;">'
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f2;padding:24px 0;">'
        '<tr><td align="center">'
        '<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">'

        # header
        '<tr><td style="background:#075e54;border-radius:14px 14px 0 0;padding:28px 32px 24px;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:rgba(255,255,255,.6);margin-bottom:6px;">{PLANT_NAME}</div>'
        '<div style="font-size:22px;font-weight:800;color:#fff;line-height:1.2;">📋 Daily Production Report</div>'
        f'<div style="font-size:14px;color:rgba(255,255,255,.7);margin-top:6px;">{date_str}</div>'
        "</td></tr>"

        # summary cards
        '<tr><td style="background:#fff;padding:20px 32px 0;">'
        '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td width="33%" style="text-align:center;padding:16px 8px;"><div style="font-size:32px;font-weight:800;color:#075e54;">{order_count}</div><div style="font-size:12px;color:#999;margin-top:4px;">Confirmed Orders</div></td>'
        f'<td width="33%" style="text-align:center;padding:16px 8px;border-left:1px solid #f0f0f0;border-right:1px solid #f0f0f0;"><div style="font-size:32px;font-weight:800;color:#075e54;">{total_items}</div><div style="font-size:12px;color:#999;margin-top:4px;">Line Items</div></td>'
        f'<td width="33%" style="text-align:center;padding:16px 8px;"><div style="font-size:32px;font-weight:800;color:{"#e67e22" if unclear_count else "#27ae60"};">{unclear_count}</div><div style="font-size:12px;color:#999;margin-top:4px;">Unclear Orders</div></td>'
        "</tr></table></td></tr>"

        # body
        '<tr><td style="background:#fff;border-radius:0 0 14px 14px;padding:24px 32px 32px;">'

        # product totals
        f'<h2 style="font-size:15px;color:#075e54;margin:8px 0 12px;font-weight:700;">📊 Product Totals</h2>'
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8faf9;border-radius:10px;border:1px solid #e0ece8;border-collapse:collapse;">'
        '<thead><tr style="background:#075e54;">'
        '<th style="padding:10px 16px;text-align:left;font-size:12px;color:#fff;">Product</th>'
        '<th style="padding:10px 16px;text-align:right;font-size:12px;color:#fff;">Total Qty</th>'
        f"</tr></thead><tbody>{product_rows}</tbody></table>"

        # order details
        f'<h2 style="font-size:15px;color:#075e54;margin:32px 0 12px;font-weight:700;">✅ Order Details {unclear_badge}</h2>'
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;border:1px solid #e0e0e0;border-collapse:collapse;">'
        '<thead><tr style="background:#f5f5f5;">'
        '<th style="padding:10px 16px;text-align:left;font-size:12px;color:#555;border-bottom:1px solid #e0e0e0;">Customer</th>'
        '<th style="padding:10px 16px;text-align:left;font-size:12px;color:#555;border-bottom:1px solid #e0e0e0;">Items</th>'
        '<th style="padding:10px 16px;text-align:left;font-size:12px;color:#555;border-bottom:1px solid #e0e0e0;">Delivery</th>'
        f"</tr></thead><tbody>{order_rows}</tbody></table>"

        f"{unclear_section}{notes_section}"

        "</td></tr>"

        # footer
        f'<tr><td style="padding:16px 32px 8px;text-align:center;font-size:11px;color:#aaa;line-height:1.7;">Generated by OrdeRR &middot; {PLANT_NAME}<br>{generated_at}</td></tr>'

        "</table></td></tr></table></body></html>"
    )


def _send_email_report(data: dict, notes: list[dict]):
    """Send the Daily Production Report as an HTML email. No-op if env vars not set."""
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
    html_body = _build_email_html(data, notes)

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
    """Send daily consolidated report to manager via approved template.
    If any customer notes were received today, send a follow-up free-form message.
    Also sends an HTML email to REPORT_EMAIL if configured."""
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

    # ── 2. Email (new) ────────────────────────────────────────────────────────
    try:
        _send_email_report(data, notes)
    except Exception as e:
        print(f"⚠️ Email report failed: {e}")