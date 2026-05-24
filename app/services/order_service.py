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
from sqlalchemy import func

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

# Multi-word filler phrases that should be rejected
FILLER_PHRASES = {
    "yes please", "yes pls", "yes sure", "yes ok", "yes okay",
    "ok sure", "ok fine", "ok thanks", "ok thank you",
    "sure sure", "fine fine", "no problem", "no worries",
    "go ahead", "please help", "help me", "i want",
    "i need", "send menu", "show menu", "place order",
    "start order", "new order", "haan ji", "haan bhai",
    "ha bhai", "ha ji", "ji haan", "ji han", "ji ha",
    "good morning", "good evening", "good night",
    "please proceed", "pls proceed", "pls help",
}


def validate_restaurant_name(name: str) -> str | None:
    """
    Returns error message string if invalid, None if valid.
    """
    stripped = name.strip()
    lower = stripped.lower()

    # Too short
    if len(stripped) < 3:
        return "That name is too short."

    # Too long
    if len(stripped) > 60:
        return "That seems too long for a restaurant name."

    # Pure numbers
    if stripped.replace(" ", "").isdigit():
        return "That looks like a number, not a restaurant name."

    # Only special characters, no letters
    if re.match(r'^[^a-zA-Z0-9\u0900-\u097F]+$', stripped):
        return "That doesn't look like a valid restaurant name."

    # Repeated characters (e.g. "aaaa", "hhhh")
    if len(set(lower.replace(" ", ""))) <= 2 and len(stripped) >= 4:
        return "That doesn't look like a valid restaurant name."

    # No vowels in long strings (gibberish detector)
    letters_only = re.sub(r'[^a-zA-Z]', '', lower)
    if len(letters_only) >= 5:
        vowels = set('aeiou')
        if not any(c in vowels for c in letters_only):
            return "That doesn't look like a valid restaurant name."

    # Exact match against single-word greetings/fillers
    if lower in GREETINGS:
        return "That looks like a greeting, not a restaurant name."

    # Match against multi-word filler phrases
    if lower in FILLER_PHRASES:
        return "That doesn't look like a restaurant name. Please send your actual restaurant or hotel name."

    # Check if ALL words in the name are filler/greeting words
    words = lower.split()
    single_word_fillers = {
        "yes", "no", "ok", "okay", "sure", "fine", "please", "pls",
        "hi", "hello", "hey", "thanks", "thank", "haan", "nahi",
        "ji", "ha", "bhai", "yep", "yup", "nope", "good", "great"
    }
    if len(words) >= 2 and all(w in single_word_fillers for w in words):
        return "That doesn't look like a restaurant name. Please send your actual restaurant or hotel name."

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
            "👋 Welcome to Fluffy Ordering System!\n\n"
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
    today = date.today()
    return db.query(Order).filter(
        func.date(Order.created_at) == today
    ).order_by(
        Order.created_at.asc()
    ).all()