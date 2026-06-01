import os
import requests

from app.services.customer_service import normalize_phone

META_ACCESS_TOKEN    = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
MANAGER_PHONE        = os.getenv("MANAGER_PHONE")
PLANT_NAME           = os.getenv("PLANT_NAME", "Fluffy")

# ── Approved template names ───────────────────────────────────────────────────
TEMPLATE_MANAGER_NEW_ORDER = "manager_new_order"


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


def _format_items(items: list) -> str:
    lines = ""
    for i, item in enumerate(items, 1):
        qty     = item["quantity"]
        qty_str = str(int(qty)) if qty == int(qty) else str(qty)
        lines  += f"{i}. {item['product']} — {qty_str} {item['unit']}\n"
    return lines


def send_order_confirmation(
    customer_phone: str,
    parsed: dict,
    restaurant_name: str = None,
) -> bool:
    """Free-form confirmation to customer — always within 24hr window."""
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
    return send_whatsapp_message(customer_phone, message) is not None


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
    {{3}} = items + delivery
    """
    items         = parsed.get("items", [])
    delivery_time = parsed.get("delivery_time", "As per usual schedule")

    items_text = _format_items(items)
    delivery_text = f"Delivery: {delivery_time}" if delivery_time else "Delivery: As per usual schedule"

    restaurant_line = f"{restaurant_name or 'Unknown'} - {customer_phone}"
    items_with_delivery = f"{items_text}{delivery_text}"

    return send_whatsapp_template(
        manager_phone,
        TEMPLATE_MANAGER_NEW_ORDER,
        [PLANT_NAME, restaurant_line, items_with_delivery],
    ) is not None


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