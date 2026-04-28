import os
import requests
from dotenv import load_dotenv

load_dotenv()

INTERAKT_API_KEY = os.getenv("INTERAKT_API_KEY")
MANAGER_PHONE = os.getenv("MANAGER_PHONE")
PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")


def send_whatsapp_message(phone: str, message: str) -> dict:
    """
    Send WhatsApp message via Interakt API.
    Currently logs to console — will send real messages when Interakt is ready.
    """

    # When Interakt is ready — uncomment this block
    # if INTERAKT_API_KEY:
    #     url = "https://api.interakt.ai/v1/public/message/"
    #     headers = {
    #         "Authorization": f"Basic {INTERAKT_API_KEY}",
    #         "Content-Type": "application/json"
    #     }
    #     payload = {
    #         "countryCode": "+91",
    #         "phoneNumber": phone,
    #         "callbackData": "order_notification",
    #         "type": "Text",
    #         "data": {
    #             "message": message
    #         }
    #     }
    #     response = requests.post(url, json=payload, headers=headers)
    #     return response.json()

    # For now — simulate sending by logging
    print(f"\n📤 WHATSAPP MESSAGE SIMULATION")
    print(f"   To      : {phone}")
    print(f"   Message : {message}")
    print(f"   Status  : ✅ Sent (simulated)\n")

    return {"status": "simulated", "phone": phone}


def send_order_confirmation(customer_phone: str, parsed: dict) -> bool:
    """
    Send order confirmation back to customer immediately after order received.
    """
    items = parsed.get("items", [])
    delivery_date = parsed.get("delivery_date", "")
    delivery_time = parsed.get("delivery_time", "")

    # Build items summary
    items_text = ""
    for item in items:
        items_text += f"• {item['product']} — {item['quantity']} {item['unit']}\n"

    # Build delivery text
    delivery_text = ""
    if delivery_date and delivery_time:
        delivery_text = f"🕐 Delivery: {delivery_date} at {delivery_time}"
    elif delivery_date:
        delivery_text = f"🕐 Delivery: {delivery_date}"
    else:
        delivery_text = "🕐 Delivery: As per usual schedule"

    # Build confirmation message
    message = f"""✅ *Order Received — {PLANT_NAME}*

{items_text}
{delivery_text}

Thank you! We will process your order shortly.
— {PLANT_NAME} Team"""

    result = send_whatsapp_message(customer_phone, message)
    return result is not None


def send_manager_alert(manager_phone: str, customer_phone: str, parsed: dict) -> bool:
    """
    Send real time order alert to plant manager immediately.
    """
    items = parsed.get("items", [])
    delivery_date = parsed.get("delivery_date", "not specified")
    delivery_time = parsed.get("delivery_time", "not specified")

    # Build items summary
    items_text = ""
    for i, item in enumerate(items, 1):
        items_text += f"{i}. {item['product']} — {item['quantity']} {item['unit']}\n"

    # Build alert message
    message = f"""🔔 *New Order — {PLANT_NAME}*

📱 Customer: {customer_phone}
📅 Delivery: {delivery_date} at {delivery_time}

📦 *Items:*
{items_text}
Please confirm and begin processing."""

    result = send_whatsapp_message(manager_phone, message)
    return result is not None


def send_unclear_order_alert(manager_phone: str, customer_phone: str, raw_message: str, unclear_reason: str) -> bool:
    """
    Alert manager when an order cannot be parsed clearly.
    """
    message = f"""⚠️ *Unclear Order — {PLANT_NAME}*

📱 Customer: {customer_phone}
💬 Message: {raw_message}

❓ Reason: {unclear_reason}

Please contact customer to clarify."""

    result = send_whatsapp_message(manager_phone, message)
    return result is not None