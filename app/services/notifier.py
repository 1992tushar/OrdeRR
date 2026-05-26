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
    parsed: dict
) -> bool:
    """
    Send order confirmation back to customer
    immediately after order received.
    """

    items = parsed.get("items", [])
    delivery_date = parsed.get("delivery_date", "")
    delivery_time = parsed.get("delivery_time", "")

    items_text = ""
    for item in items:
        items_text += (
            f"• {item['product']} "
            f"— {item['quantity']} "
            f"{item['unit']}\n"
        )

    if delivery_date and delivery_time:
        delivery_text = f"🕐 Delivery: {delivery_date} at {delivery_time}"
    elif delivery_date:
        delivery_text = f"🕐 Delivery: {delivery_date}"
    else:
        delivery_text = "🕐 Delivery: As per usual schedule"

    message = (
        f"✅ *Order Received — {PLANT_NAME}*\n\n"
        f"{items_text}\n"
        f"{delivery_text}\n\n"
        f"Thank you! We will process your order shortly.\n\n"
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


def send_interactive_list(phone: str, items: list) -> dict:
    """
    Send WhatsApp interactive list message — customer taps to select a product.
    items: list of dicts with keys: id, title, description
    """
    if not (META_ACCESS_TOKEN and META_PHONE_NUMBER_ID):
        print(f"\n📤 SIMULATION — Interactive list to: {phone}")
        return {"status": "simulated"}

    try:
        clean_phone = normalize_phone(phone)
        url = f"https://graph.facebook.com/v21.0/{META_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {META_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": f"🐔 {PLANT_NAME} — Place Order"},
                "body":   {"text": "Tap an item to add it to your order."},
                "footer": {"text": "You can add multiple items."},
                "action": {
                    "button": "View Products",
                    "sections": [
                        {
                            "title": "Our Products",
                            "rows": [
                                {
                                    "id":          item["id"],
                                    "title":       item["title"],
                                    "description": item.get("description", "")
                                }
                                for item in items
                            ]
                        }
                    ]
                }
            }
        }
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        print(f"\n📤 Interactive list sent → {clean_phone} | status: {response.status_code}")
        if response.status_code >= 400:
            print(f"❌ Meta API Error: {response.text}")
        return response.json()

    except Exception as e:
        print(f"❌ Interactive list send failed: {str(e)}")
        return None


def send_quick_reply_buttons(phone: str, body_text: str, buttons: list) -> dict:
    """
    Send WhatsApp quick-reply buttons — customer taps one.
    buttons: list of dicts with keys: id, title (max 3 buttons, title max 20 chars)
    """
    if not (META_ACCESS_TOKEN and META_PHONE_NUMBER_ID):
        print(f"\n📤 SIMULATION — Quick reply to: {phone}")
        print(f"   Body: {body_text}")
        print(f"   Buttons: {buttons}")
        return {"status": "simulated"}

    try:
        clean_phone = normalize_phone(phone)
        url = f"https://graph.facebook.com/v21.0/{META_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {META_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {
                            "type":  "reply",
                            "reply": {"id": btn["id"], "title": btn["title"]}
                        }
                        for btn in buttons[:3]  # Meta max is 3
                    ]
                }
            }
        }
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        print(f"\n📤 Quick reply sent → {clean_phone} | status: {response.status_code}")
        if response.status_code >= 400:
            print(f"❌ Meta API Error: {response.text}")
        return response.json()

    except Exception as e:
        print(f"❌ Quick reply send failed: {str(e)}")
        return None