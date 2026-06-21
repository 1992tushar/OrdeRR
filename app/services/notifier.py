import logging
import os
import requests

from app.services.customer_service import normalize_phone

META_ACCESS_TOKEN    = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
MANAGER_PHONE        = os.getenv("MANAGER_PHONE")
PLANT_NAME           = os.getenv("PLANT_NAME", "Fluffy")

logger = logging.getLogger(__name__)

# ── Approved template names ───────────────────────────────────────────────────
TEMPLATE_MANAGER_NEW_ORDER         = "manager_new_order"
TEMPLATE_CUSTOMER_REGISTRATION     = "customer_registration_welcome_v2"
TEMPLATE_SALESPERSON_REGISTRATION  = "salesperson_registration_welcome"


# ── Send helper ───────────────────────────────────────────────────────────────

def _send_and_log(send_fn, recipient: str, label: str, *args, **kwargs) -> bool:
    """
    Call send_fn(recipient, *args, **kwargs), log failures, return True/False.
    Use this wherever order.confirmation_sent or similar boolean columns are set.
    """
    try:
        result = send_fn(recipient, *args, **kwargs)
        if result is None:
            logger.error(f"WA FAIL [{label}] to {recipient}: send returned None")
            return False
        if isinstance(result, dict) and result.get("error"):
            logger.error(f"WA FAIL [{label}] to {recipient}: {result['error']}")
            return False
        return True
    except Exception as e:
        logger.error(f"WA EXCEPTION [{label}] to {recipient}: {e}")
        return False


def send_whatsapp_message(phone: str, message: str) -> dict:
    """Send free-form WhatsApp message. Works only within 24hr customer window."""
    if META_ACCESS_TOKEN and META_PHONE_NUMBER_ID:
        try:
            clean_phone = normalize_phone(phone)
            url = f"https://graph.facebook.com/v21.0/{META_PHONE_NUMBER_ID}/messages"
            headers = {
                "Authorization": f"Bearer {META_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": clean_phone,
                "type": "text",
                "text": {"body": message},
            }
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            print(f"\n📤 WhatsApp sent via Meta Cloud API")
            print(f"   To       : {clean_phone}")
            print(f"   Status   : {response.status_code}")
            if response.status_code >= 400:
                print(f"❌ Meta API Error: {response.text}")
            return response.json()
        except Exception as e:
            print(f"❌ Meta API send failed: {str(e)}")
            return None
    else:
        print(f"\n📤 SIMULATION — To: {phone}")
        print(f"   Message: {message}\n")
        return {"status": "simulated"}


def send_whatsapp_template(phone: str, template_name: str, parameters: list) -> dict:
    """
    Send an approved WhatsApp template message.
    Works anytime — no 24hr window required.

    parameters: list of string values for {{1}}, {{2}}, {{3}} etc.
    """
    if META_ACCESS_TOKEN and META_PHONE_NUMBER_ID:
        try:
            clean_phone = normalize_phone(phone)
            url = f"https://graph.facebook.com/v21.0/{META_PHONE_NUMBER_ID}/messages"
            headers = {
                "Authorization": f"Bearer {META_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": clean_phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": "en"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": str(p)}
                                for p in parameters
                            ],
                        }
                    ],
                },
            }
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            print(f"\n📤 WhatsApp template sent: {template_name}")
            print(f"   To       : {clean_phone}")
            print(f"   Status   : {response.status_code}")
            if response.status_code >= 400:
                print(f"❌ Meta API Error: {response.text}")
            return response.json()
        except Exception as e:
            print(f"❌ Meta API template send failed: {str(e)}")
            return None
    else:
        print(f"\n📤 SIMULATION — Template: {template_name} → To: {phone}")
        print(f"   Params: {parameters}\n")
        return {"status": "simulated"}


# ── Registration welcome templates ────────────────────────────────────────────

def send_customer_registration_welcome(phone: str, plant_name: str) -> dict:
    """
    Send customer registration welcome via approved template.
    Template: customer_registration_welcome_v2
    {{1}} = plant_name
    """
    return send_whatsapp_template(
        phone,
        TEMPLATE_CUSTOMER_REGISTRATION,
        [plant_name],
    )


def send_salesperson_registration_welcome(phone: str, name: str, area: str) -> dict:
    """
    Send salesperson registration welcome via approved template.
    Template: salesperson_registration_welcome
    {{1}} = salesperson name
    {{2}} = area
    """
    return send_whatsapp_template(
        phone,
        TEMPLATE_SALESPERSON_REGISTRATION,
        [name, area],
    )


# ── Order notifications ───────────────────────────────────────────────────────

def _format_items_freeform(items: list) -> str:
    """Newline-separated item list for free-form WhatsApp messages."""
    lines = ""
    for i, item in enumerate(items, 1):
        qty     = item["quantity"]
        qty_str = str(int(qty)) if qty == int(qty) else str(qty)
        lines  += f"{i}. {item['product']} — {qty_str} {item['unit']}\n"
    return lines


def _format_items_template(items: list) -> str:
    """Pipe-separated item list for Meta template parameters (no newlines allowed)."""
    parts = []
    for item in items:
        qty     = item["quantity"]
        qty_str = str(int(qty)) if qty == int(qty) else str(qty)
        parts.append(f"{item['product']} {qty_str} {item['unit']}")
    return " | ".join(parts)


def send_order_confirmation(
    customer_phone: str,
    parsed: dict,
    restaurant_name: str = None,
) -> bool:
    """
    Free-form confirmation to customer — always within 24hr window.
    Returns True if the message was sent successfully, False otherwise.
    """
    items         = parsed.get("items", [])
    delivery_time = parsed.get("delivery_time", "")

    items_text = ""
    for item in items:
        qty     = item["quantity"]
        qty_str = str(int(qty)) if qty == int(qty) else str(qty)
        items_text += f"• {item['product']} — {qty_str} {item['unit']}\n"

    delivery_text = (
        f"🕒 Delivery: {delivery_time}"
        if delivery_time
        else "🕒 Delivery: As per usual schedule"
    )
    name_line = f"🏪 {restaurant_name}\n\n" if restaurant_name else ""

    message = (
        f"✅ *Order Confirmed — {PLANT_NAME}*\n\n"
        f"{name_line}"
        f"{items_text}\n"
        f"{delivery_text}\n\n"
        f"📞 Contact us if you need to make any changes.\n\n"
        f"— {PLANT_NAME} Team"
    )
    return _send_and_log(send_whatsapp_message, customer_phone, "order_confirmation", message)


def send_manager_alert(
    manager_phone: str,
    customer_phone: str,
    parsed: dict,
    restaurant_name: str = None,
) -> bool:
    """
    Send new order alert to manager via approved template.
    Template: manager_new_order
    {{1}} = PLANT_NAME
    {{2}} = restaurant name + phone
    {{3}} = items + delivery — pipe-separated, no newlines (Meta requirement)
    """
    items         = parsed.get("items", [])
    delivery_time = parsed.get("delivery_time", "As per usual schedule")

    items_text    = _format_items_template(items)
    delivery_text = f"Delivery: {delivery_time}" if delivery_time else "Delivery: As per usual schedule"

    restaurant_line      = f"{restaurant_name or 'Unknown'} - {customer_phone}"
    items_with_delivery  = f"{items_text} | {delivery_text}"

    return send_whatsapp_template(
        manager_phone,
        TEMPLATE_MANAGER_NEW_ORDER,
        [PLANT_NAME, restaurant_line, items_with_delivery],
    ) is not None


def notify_manager_new_order(
    customer_phone: str,
    parsed: dict,
    restaurant_name: str = None,
) -> bool:
    """
    Free-form-message convenience wrapper for alerting the manager about a
    new order. Sends via send_whatsapp_message (not the approved-template
    path) to MANAGER_PHONE. send_manager_alert (the approved-template
    variant with a different signature) is untouched and still used by the
    main order flow — this is an additional, simpler notifier expected by
    callers/tests that just want a quick free-form heads-up.
    """
    items         = parsed.get("items", [])
    delivery_time = parsed.get("delivery_time", "As per usual schedule")

    items_text    = _format_items_freeform(items)
    delivery_text = f"🕒 Delivery: {delivery_time}" if delivery_time else "🕒 Delivery: As per usual schedule"
    name_line     = f"🏪 {restaurant_name}\n" if restaurant_name else ""

    message = (
        f"🆕 *New Order — {PLANT_NAME}*\n\n"
        f"{name_line}"
        f"📱 {customer_phone}\n\n"
        f"{items_text}\n"
        f"{delivery_text}"
    )
    return send_whatsapp_message(MANAGER_PHONE, message) is not None


def send_unclear_order_alert(
    manager_phone: str,
    customer_phone: str,
    raw_message: str,
    unclear_reason: str,
) -> bool:
    """Free-form alert — manager always within window via daily interactions."""
    message = (
        f"⚠️ *Unclear Order — {PLANT_NAME}*\n\n"
        f"📱 Customer: {customer_phone}\n"
        f"💬 Message: {raw_message}\n\n"
        f"❓ Reason: {unclear_reason}\n\n"
        f"Please contact customer to clarify."
    )
    return send_whatsapp_message(manager_phone, message) is not None


def send_replace_confirmation_request(
    customer_phone: str,
    existing_items: list,
    new_items: list,
) -> bool:
    def fmt(items):
        lines = ""
        for item in items:
            qty     = item["quantity"]
            qty_str = str(int(qty)) if qty == int(qty) else str(qty)
            lines  += f"• {item['product']} — {qty_str} {item['unit']}\n"
        return lines

    message = (
        f"⚠️ *You already placed an order today — {PLANT_NAME}*\n\n"
        f"*Current order:*\n{fmt(existing_items)}\n"
        f"*New order:*\n{fmt(new_items)}\n"
        f"Reply *yes* to replace your order, or *no* to keep the current one."
    )
    return send_whatsapp_message(customer_phone, message) is not None


def send_repeat_order_confirmation_request(
    customer_phone: str,
    items: list,
) -> bool:
    def fmt(items):
        lines = ""
        for item in items:
            qty     = item["quantity"]
            qty_str = str(int(qty)) if qty == int(qty) else str(qty)
            lines  += f"• {item['product']} — {qty_str} {item['unit']}\n"
        return lines

    message = (
        f"🔁 *Repeat Last Order — {PLANT_NAME}*\n\n"
        f"Your last order was:\n\n"
        f"{fmt(items)}\n"
        f"Reply *yes* to confirm, or type *order* to place a new one."
    )
    return send_whatsapp_message(customer_phone, message) is not None


def send_manager_menu(phone: str) -> dict:
    """
    Send an interactive Quick Reply menu to the manager.
    Buttons: Summary | Daily Report | Add Customer
    """
    if META_ACCESS_TOKEN and META_PHONE_NUMBER_ID:
        try:
            clean_phone = normalize_phone(phone)
            url = f"https://graph.facebook.com/v21.0/{META_PHONE_NUMBER_ID}/messages"
            headers = {
                "Authorization": f"Bearer {META_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": clean_phone,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {
                        "text": f"👔 *{PLANT_NAME} Manager Menu*\n\nWhat would you like to do?"
                    },
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": "mgr_summary",
                                    "title": "📊 Summary"
                                }
                            },
                            {
                                "type": "reply",
                                "reply": {
                                    "id": "mgr_daily_report",
                                    "title": "📋 Daily Report"
                                }
                            },
                            {
                                "type": "reply",
                                "reply": {
                                    "id": "mgr_add_customer",
                                    "title": "➕ Add Customer"
                                }
                            }
                        ]
                    }
                }
            }
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            print(f"\n📤 Manager menu sent → {clean_phone} ({response.status_code})")
            if response.status_code >= 400:
                print(f"❌ Meta API Error: {response.text}")
            return response.json()
        except Exception as e:
            print(f"❌ Manager menu send failed: {str(e)}")
            return None
    else:
        print(f"\n📤 SIMULATION — Manager menu → {phone}")
        return {"status": "simulated"}


def send_salesperson_menu(phone: str, name: str = "there") -> dict:
    """
    Send an interactive Quick Reply menu to a salesperson.
    Buttons: My Pending | Help
    """
    if META_ACCESS_TOKEN and META_PHONE_NUMBER_ID:
        try:
            clean_phone = normalize_phone(phone)
            url = f"https://graph.facebook.com/v21.0/{META_PHONE_NUMBER_ID}/messages"
            headers = {
                "Authorization": f"Bearer {META_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": clean_phone,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {
                        "text": f"🧑 *{PLANT_NAME} — Hi {name}!*\n\nWhat would you like to do?"
                    },
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": "sp_pending",
                                    "title": "📋 My Pending"
                                }
                            },
                            {
                                "type": "reply",
                                "reply": {
                                    "id": "sp_help",
                                    "title": "❓ Help"
                                }
                            }
                        ]
                    }
                }
            }
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            print(f"\n📤 Salesperson menu sent → {clean_phone} ({response.status_code})")
            if response.status_code >= 400:
                print(f"❌ Meta API Error: {response.text}")
            return response.json()
        except Exception as e:
            print(f"❌ Salesperson menu send failed: {str(e)}")
            return None
    else:
        print(f"\n📤 SIMULATION — Salesperson menu → {phone} (name={name})")
        return {"status": "simulated"}