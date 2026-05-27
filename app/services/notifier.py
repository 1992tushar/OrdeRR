import os
import requests

from app.services.customer_service import normalize_phone

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
MANAGER_PHONE = os.getenv("MANAGER_PHONE")
PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")


def send_whatsapp_message(
    phone: str,
    message: str
) -> dict:
    """
    Send WhatsApp message via Meta Cloud API.
    Uses shared normalize_phone() — no duplicate logic.
    """

    if META_ACCESS_TOKEN and META_PHONE_NUMBER_ID:

        try:
            clean_phone = normalize_phone(phone)

            url = (
                f"https://graph.facebook.com/"
                f"v21.0/"
                f"{META_PHONE_NUMBER_ID}/messages"
            )

            headers = {
                "Authorization": f"Bearer {META_ACCESS_TOKEN}",
                "Content-Type": "application/json"
            }

            payload = {
                "messaging_product": "whatsapp",
                "to": clean_phone,
                "type": "text",
                "text": {"body": message}
            }

            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=15
            )

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


def send_order_confirmation(
    customer_phone: str,
    parsed: dict,
    restaurant_name: str = None,
) -> bool:

    items         = parsed.get("items", [])
    delivery_time = parsed.get("delivery_time", "")

    items_text = ""
    for item in items:
        qty = item["quantity"]
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

    result = send_whatsapp_message(customer_phone, message)
    return result is not None


def send_manager_alert(
    manager_phone: str,
    customer_phone: str,
    parsed: dict,
    restaurant_name: str = None
) -> bool:
    """
    Send real time order alert to plant manager immediately.
    """

    items = parsed.get("items", [])
    delivery_date = parsed.get("delivery_date", "not specified")
    delivery_time = parsed.get("delivery_time", "not specified")

    items_text = ""
    for i, item in enumerate(items, 1):
        items_text += (
            f"{i}. {item['product']} — "
            f"{item['quantity']} {item['unit']}\n"
        )

    message = (
        f"🔔 *New Order — {PLANT_NAME}*\n\n"
        f"🏪 Restaurant: {restaurant_name or 'Unknown Restaurant'}\n"
        f"📱 Customer: {customer_phone}\n"
        f"📅 Delivery: {delivery_date} at {delivery_time}\n\n"
        f"📦 *Items:*\n{items_text}\n\n"
        f"Please confirm and begin processing."
    )

    result = send_whatsapp_message(manager_phone, message)
    return result is not None


def send_unclear_order_alert(
    manager_phone: str,
    customer_phone: str,
    raw_message: str,
    unclear_reason: str
) -> bool:
    """
    Alert manager when an order cannot be parsed clearly.
    """

    message = (
        f"⚠️ *Unclear Order — {PLANT_NAME}*\n\n"
        f"📱 Customer: {customer_phone}\n"
        f"💬 Message: {raw_message}\n\n"
        f"❓ Reason: {unclear_reason}\n\n"
        f"Please contact customer to clarify."
    )

    result = send_whatsapp_message(manager_phone, message)
    return result is not None


def send_replace_confirmation_request(
    customer_phone: str,
    existing_items: list,
    new_items: list,
) -> bool:
    """
    Ask customer if they want to replace their existing order.
    Called when a second order arrives on the same day.
    """
    def fmt(items):
        lines = ""
        for item in items:
            qty = item["quantity"]
            qty_str = str(int(qty)) if qty == int(qty) else str(qty)
            lines += f"• {item['product']} — {qty_str} {item['unit']}\n"
        return lines

    message = (
        f"⚠️ *You already placed an order today — {PLANT_NAME}*\n\n"
        f"*Current order:*\n{fmt(existing_items)}\n"
        f"*New order:*\n{fmt(new_items)}\n"
        f"Reply *yes* to replace your order, or *no* to keep the current one."
    )
    result = send_whatsapp_message(customer_phone, message)
    return result is not None


def send_repeat_order_confirmation_request(
    customer_phone: str,
    items: list,
) -> bool:
    """
    Ask customer to confirm repeating their last order.
    Called when customer sends 'same' or 'repeat'.
    """
    def fmt(items):
        lines = ""
        for item in items:
            qty = item["quantity"]
            qty_str = str(int(qty)) if qty == int(qty) else str(qty)
            lines += f"• {item['product']} — {qty_str} {item['unit']}\n"
        return lines

    message = (
        f"🔁 *Repeat Last Order — {PLANT_NAME}*\n\n"
        f"Your last order was:\n\n"
        f"{fmt(items)}\n"
        f"Reply *yes* to confirm, or type *order* to place a new one."
    )
    result = send_whatsapp_message(customer_phone, message)
    return result is not None