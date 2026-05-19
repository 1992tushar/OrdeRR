from sqlalchemy.orm import Session
from sqlalchemy import cast, Date, func

from app.models.order import Order
from app.services.parser import parse_order
from app.services.notifier import (
    send_order_confirmation,
    send_manager_alert,
    send_unclear_order_alert
)
from app.services.template_parser import parse_template_order
from app.services.customer_service import (
    get_customer_by_phone,
    create_new_customer
)

import json
import re
import os

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")

GREETINGS = {
    "hi", "hello", "hey", "hii", "hiii", "hiiii",
    "helo", "helloo",
    "ok", "okay", "okk", "okkk",
    "haan", "han", "ha", "haa",
    "yes", "no", "nahi", "nope", "yep", "yup",
    "thanks", "thank you", "thankyou", "thnx", "thx",
    "bye", "goodbye", "good morning", "good evening",
    "good night", "goodnight", "gm", "gn",
    "namaste", "namaskar", "jai hind",
    "test", "testing", "hello world",
    "who", "what", "where", "when", "why", "how",
}


def validate_restaurant_name(name: str) -> str | None:
    if len(name) < 3:
        return "That name is too short."
    if len(name) > 60:
        return "That seems too long for a restaurant name."
    if name.replace(" ", "").isdigit():
        return "That looks like a number, not a restaurant name."
    if name.lower().strip() in GREETINGS:
        return "That looks like a greeting, not a restaurant name."
    if re.match(r'^[^a-zA-Z0-9\u0900-\u097F]+$', name):
        return "That doesn't look like a valid restaurant name."
    if len(set(name.lower().replace(" ", ""))) <= 2 and len(name) >= 4:
        return "That doesn't look like a valid restaurant name."
    letters_only = re.sub(r'[^a-zA-Z]', '', name.lower())
    if len(letters_only) >= 5:
        vowels = set('aeiou')
        if not any(c in vowels for c in letters_only):
            return "That doesn't look like a valid restaurant name."
    return None


def process_incoming_order(
    db: Session,
    customer_phone: str,
    message: str,
    is_photo: bool = False
) -> dict:
    """
    Full pipeline:
    1. Parse incoming WhatsApp message
    2. Save to database
    3. Send confirmation to customer
    4. Alert plant manager
    """

    from app.services.notifier import send_whatsapp_message

    # Lookup customer
    customer = get_customer_by_phone(db, customer_phone)

    # First-time customer
    if not customer:
        customer = create_new_customer(db, customer_phone)

        send_whatsapp_message(
            customer_phone,
            "👋 Welcome to BBC Ordering!\n\n"
            "Please reply with your *restaurant or hotel name* to continue."
        )

        return {
            "order_id": None,
            "status": "awaiting_restaurant_name",
            "parsed": None
        }

    # Customer onboarding pending
    if customer.onboarding_status == "awaiting_name":

        name = message.strip()
        validation_error = validate_restaurant_name(name)

        if validation_error:
            send_whatsapp_message(
                customer_phone,
                f"⚠️ {validation_error}\n\n"
                f"Please reply with your *restaurant or hotel name* to continue."
            )
            return {
                "order_id": None,
                "status": "invalid_restaurant_name",
                "parsed": None
            }

        customer.restaurant_name = name
        customer.onboarding_status = "active"
        db.commit()

        send_whatsapp_message(
            customer_phone,
            f"✅ Welcome *{customer.restaurant_name}*!\n\n"
            f"You can now place your orders.\n\n"
            f"Type *order* to see our menu."
        )

        return {
            "order_id": None,
            "status": "customer_onboarded",
            "parsed": None
        }

    # Active customer — process order
    restaurant_name = customer.restaurant_name

    # Detect template-style structured order
    template_keywords = [
        "whole broiler",
        "breast boneless",
        "leg boneless",
        "wings",
        "drumsticks"
    ]

    is_template_order = any(
        keyword in message.lower()
        for keyword in template_keywords
    )

    if is_template_order:
        parsed = parse_template_order(customer_phone, message)
        # Fallback to AI parser if template parse failed
        if parsed.get("is_unclear"):
            parsed = parse_order(customer_phone, message)
    else:
        parsed = parse_order(customer_phone, message)

    # Save order
    order = Order(
        plant_name=os.getenv("PLANT_NAME", "Fluffy"),
        customer_name=restaurant_name,
        customer_phone=customer_phone,
        raw_message=message,
        is_photo_order=is_photo,
        parsed_items=json.dumps(parsed.get("items", [])),
        delivery_date=parsed.get("delivery_date"),
        delivery_time=parsed.get("delivery_time"),
        is_unclear=parsed.get("is_unclear", False),
        unclear_reason=parsed.get("unclear_reason"),
        status="received"
    )

    db.add(order)
    db.commit()
    db.refresh(order)

    # Send alerts
    if parsed.get("is_unclear"):
        send_unclear_order_alert(
            manager_phone=MANAGER_PHONE,
            customer_phone=customer_phone,
            raw_message=message,
            unclear_reason=parsed.get("unclear_reason", "Unknown reason")
        )
    else:
        send_order_confirmation(
            customer_phone=customer_phone,
            parsed=parsed
        )
        send_manager_alert(
            manager_phone=MANAGER_PHONE,
            customer_phone=customer_phone,
            parsed=parsed,
            restaurant_name=restaurant_name
        )

    order.confirmation_sent = True
    order.forwarded_to_manager = True
    db.commit()

    return {
        "order_id": order.id,
        "customer_phone": customer_phone,
        "parsed": parsed,
        "status": order.status,
        "saved": True
    }


def get_all_orders(db: Session) -> list:
    return db.query(Order).order_by(
        Order.created_at.desc()
    ).all()


def get_unclear_orders(db: Session) -> list:
    return db.query(Order).filter(
        Order.is_unclear.is_(True)
    ).order_by(
        Order.created_at.desc()
    ).all()


def get_todays_orders(db: Session) -> list:
    return db.query(Order).filter(
        cast(Order.created_at, Date) == func.current_date()
    ).order_by(
        Order.created_at.asc()
    ).all()