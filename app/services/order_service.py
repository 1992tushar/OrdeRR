import json
import os
import re
from datetime import date, datetime, timezone, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.order import Order
from app.models.customer import Customer
from app.services.parser import parse_order
from app.services.notifier import (
    send_order_confirmation,
    send_manager_alert,
    send_unclear_order_alert,
    send_whatsapp_message,
)
from app.services.template_parser import parse_template_order
from app.services.customer_service import get_customer_by_phone, create_new_customer

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME    = os.getenv("PLANT_NAME", "Fluffy")
IST           = timezone(timedelta(hours=5, minutes=30))

# ── Keyword sets ──────────────────────────────────────────────────────────────

CANCEL_KEYWORDS = {
    "cancel", "cancel order", "cancel my order", "order cancel",
    "cancel karo", "cancel kar", "band karo", "mat bhejo",
    "order band", "no order", "no order today", "aaj nahi",
    "order nahi", "nahi chahiye",
}

EDIT_PREFIXES = [
    "change order", "edit order", "update order", "modify order",
    "order change", "order update", "order modify",
    "change karo", "update karo", "change kar",
    "galat order", "wrong order", "correction",
]

MENU_KEYWORDS = {
    "menu", "order", "show menu", "send menu", "place order",
    "kya hai", "product", "list", "rate", "rate list",
}

GREETINGS = {
    "hi", "hello", "hey", "hii", "hiii", "hiiii", "helo", "helloo",
    "ok", "okay", "okk", "okkk", "haan", "han", "ha", "haa",
    "yes", "no", "nahi", "nope", "yep", "yup",
    "thanks", "thank you", "thankyou", "thnx", "thx",
    "bye", "goodbye", "good morning", "good evening",
    "good night", "goodnight", "gm", "gn",
    "namaste", "namaskar", "jai hind",
    "test", "testing", "hello world",
    "who", "what", "where", "when", "why", "how",
}

FILLER_PHRASES = {
    "yes please", "yes pls", "yes sure", "yes ok", "yes okay",
    "ok sure", "ok fine", "ok thanks", "ok thank you",
    "sure sure", "fine fine", "no problem", "no worries",
    "go ahead", "please help", "help me", "i want", "i need",
    "send menu", "show menu", "place order", "start order", "new order",
    "haan ji", "haan bhai", "ha bhai", "ha ji", "ji haan", "ji han", "ji ha",
    "good morning", "good evening", "good night",
    "please proceed", "pls proceed", "pls help",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_today_ist() -> date:
    return datetime.now(IST).date()


def get_delivery_date_str() -> str:
    """Returns today's date as YYYY-MM-DD. No cutoff logic — plant decides."""
    return get_today_ist().strftime("%Y-%m-%d")


def get_todays_active_order(db: Session, customer_phone: str) -> Order | None:
    """Most recent non-cancelled order placed today."""
    today_str = get_today_ist().strftime("%Y-%m-%d")
    return (
        db.query(Order)
        .filter(
            Order.customer_phone == customer_phone,
            Order.delivery_date  == today_str,
            Order.is_cancelled   == False,
        )
        .order_by(Order.created_at.desc())
        .first()
    )


def validate_restaurant_name(name: str) -> str | None:
    stripped = name.strip()
    lower    = stripped.lower()

    if len(stripped) < 3:
        return "That name is too short."
    if len(stripped) > 60:
        return "That seems too long for a restaurant name."
    if stripped.replace(" ", "").isdigit():
        return "That looks like a number, not a restaurant name."
    if re.match(r'^[^a-zA-Z0-9\u0900-\u097F]+$', stripped):
        return "That doesn't look like a valid restaurant name."
    if len(set(lower.replace(" ", ""))) <= 2 and len(stripped) >= 4:
        return "That doesn't look like a valid restaurant name."

    letters_only = re.sub(r'[^a-zA-Z]', '', lower)
    if len(letters_only) >= 5 and not any(c in 'aeiou' for c in letters_only):
        return "That doesn't look like a valid restaurant name."

    if lower in GREETINGS:
        return "That looks like a greeting, not a restaurant name."
    if lower in FILLER_PHRASES:
        return "That doesn't look like a restaurant name. Please send your actual restaurant or hotel name."

    single_word_fillers = {
        "yes", "no", "ok", "okay", "sure", "fine", "please", "pls",
        "hi", "hello", "hey", "thanks", "thank", "haan", "nahi",
        "ji", "ha", "bhai", "yep", "yup", "nope", "good", "great",
    }
    words = lower.split()
    if len(words) >= 2 and all(w in single_word_fillers for w in words):
        return "That doesn't look like a restaurant name. Please send your actual restaurant or hotel name."

    return None


# ── Main pipeline ─────────────────────────────────────────────────────────────

def process_incoming_order(
    db: Session,
    customer_phone: str,
    message: str,
    is_photo: bool = False,
) -> dict:

    msg_lower = message.strip().lower()

    # ── 1. Lookup / create customer ───────────────────────────────────────────
    customer = get_customer_by_phone(db, customer_phone)

    if not customer:
        customer = create_new_customer(db, customer_phone)
        send_whatsapp_message(
            customer_phone,
            f"👋 Welcome to *{PLANT_NAME}* Ordering System!\n\n"
            "Please reply with your *restaurant or hotel name* to continue.",
        )
        return {"order_id": None, "status": "awaiting_restaurant_name", "parsed": None}

    # ── 2. Onboarding ─────────────────────────────────────────────────────────
    if customer.onboarding_status == "awaiting_name":
        error = validate_restaurant_name(message.strip())
        if error:
            send_whatsapp_message(
                customer_phone,
                f"⚠️ {error}\n\n"
                "Please reply with your *restaurant or hotel name* to continue.",
            )
            return {"order_id": None, "status": "invalid_restaurant_name", "parsed": None}

        customer.restaurant_name   = message.strip()
        customer.onboarding_status = "active"
        db.commit()

        send_whatsapp_message(
            customer_phone,
            f"✅ Welcome *{customer.restaurant_name}*!\n\n"
            f"You can now place your orders.\n\n"
            f"Type *order* to see our menu.",
        )

        # Alert manager about new customer
        try:
            send_whatsapp_message(
                MANAGER_PHONE,
                f"🆕 *New Customer Registered — {PLANT_NAME}*\n\n"
                f"🏪 {customer.restaurant_name}\n"
                f"📱 {customer_phone}\n\n"
                f"Please assign area and salesperson on the dashboard.",
            )
        except Exception as e:
            print(f"⚠️ New customer manager alert failed: {e}")

        return {"order_id": None, "status": "customer_onboarded", "parsed": None}

    # ── 3. Menu on demand ─────────────────────────────────────────────────────
    if msg_lower in MENU_KEYWORDS:
        from app.services.product_catalog import generate_menu_template
        send_whatsapp_message(customer_phone, generate_menu_template())
        return {"order_id": None, "status": "menu_sent", "parsed": None}

    # ── 4. Cancel order ───────────────────────────────────────────────────────
    if msg_lower in CANCEL_KEYWORDS:
        existing = get_todays_active_order(db, customer_phone)
        if not existing:
            send_whatsapp_message(
                customer_phone,
                "ℹ️ No active order found for today to cancel.",
            )
            return {"order_id": None, "status": "no_order_to_cancel", "parsed": None}

        existing.is_cancelled = True
        existing.cancelled_at = datetime.now(IST)
        existing.status       = "cancelled"
        db.commit()

        send_whatsapp_message(
            customer_phone,
            "✅ Your order has been cancelled.\n\n"
            "If you need to place a new order, just send it anytime.",
        )
        try:
            send_whatsapp_message(
                MANAGER_PHONE,
                f"❌ *Order Cancelled — {PLANT_NAME}*\n\n"
                f"🏪 {customer.restaurant_name}\n"
                f"📱 {customer_phone}\n\n"
                f"Their order for today has been cancelled.",
            )
        except Exception:
            pass

        return {"order_id": existing.id, "status": "order_cancelled", "parsed": None}

    # ── 5. Detect edit intent ─────────────────────────────────────────────────
    is_edit = False
    for kw in EDIT_PREFIXES:
        if msg_lower == kw or msg_lower.startswith(kw + " ") or msg_lower.startswith(kw + "\n"):
            is_edit = True
            # Strip the edit keyword so we parse only the new order text
            tail = message.strip()[len(kw):].strip()
            if tail:
                message   = tail
                msg_lower = tail.lower()
            break

    # ── 6. Parse order ────────────────────────────────────────────────────────
    restaurant_name = customer.restaurant_name
    template_keywords = ["whole broiler", "breast boneless", "leg boneless", "wings", "drumsticks"]
    is_template = any(kw in msg_lower for kw in template_keywords)

    if is_template:
        parsed = parse_template_order(customer_phone, message)
        if parsed.get("is_unclear"):
            parsed = parse_order(customer_phone, message)
    else:
        parsed = parse_order(customer_phone, message)

    existing_order = get_todays_active_order(db, customer_phone)

    # ── 7. Cancel old order on edit ───────────────────────────────────────────
    if is_edit and existing_order:
        existing_order.is_cancelled = True
        existing_order.cancelled_at = datetime.now(IST)
        existing_order.status       = "cancelled"
        db.commit()

    # ── 8. Duplicate detection (not an edit) ──────────────────────────────────
    if not is_edit and existing_order and not parsed.get("is_unclear"):
        try:
            send_whatsapp_message(
                MANAGER_PHONE,
                f"⚠️ *Duplicate Order — {PLANT_NAME}*\n\n"
                f"🏪 {restaurant_name}\n"
                f"📱 {customer_phone}\n\n"
                f"This customer already placed an order today.\n"
                f"New order saved — please check dashboard.",
            )
        except Exception:
            pass

    # ── 9. Save order ─────────────────────────────────────────────────────────
    order = Order(
        plant_name     = PLANT_NAME,
        customer_name  = restaurant_name,
        customer_phone = customer_phone,
        raw_message    = message,
        is_photo_order = is_photo,
        parsed_items   = json.dumps(parsed.get("items", [])),
        delivery_date  = get_delivery_date_str(),
        delivery_time  = parsed.get("delivery_time"),
        is_unclear     = parsed.get("is_unclear", False),
        unclear_reason = parsed.get("unclear_reason"),
        status         = "received",
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    # ── 10. Send confirmations ────────────────────────────────────────────────
    if parsed.get("is_unclear"):
        send_unclear_order_alert(
            manager_phone  = MANAGER_PHONE,
            customer_phone = customer_phone,
            raw_message    = message,
            unclear_reason = parsed.get("unclear_reason", "Unknown reason"),
        )
    elif is_edit:
        items_text = "\n".join(
            f"• {i['product']} — {i['quantity']} {i['unit']}"
            for i in parsed.get("items", [])
        )
        send_whatsapp_message(
            customer_phone,
            f"✅ *Order Updated — {PLANT_NAME}*\n\n"
            f"Your previous order has been replaced with:\n\n"
            f"{items_text}\n\nThank you!",
        )
        try:
            send_whatsapp_message(
                MANAGER_PHONE,
                f"✏️ *Order Updated — {PLANT_NAME}*\n\n"
                f"🏪 {restaurant_name}\n📱 {customer_phone}\n\n"
                f"New order:\n{items_text}",
            )
        except Exception:
            pass
    else:
        send_order_confirmation(customer_phone=customer_phone, parsed=parsed)
        send_manager_alert(
            manager_phone   = MANAGER_PHONE,
            customer_phone  = customer_phone,
            parsed          = parsed,
            restaurant_name = restaurant_name,
        )

    order.confirmation_sent    = True
    order.forwarded_to_manager = True
    db.commit()

    return {
        "order_id"      : order.id,
        "customer_phone": customer_phone,
        "parsed"        : parsed,
        "status"        : order.status,
        "is_edit"       : is_edit,
        "saved"         : True,
    }


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_all_orders(db: Session) -> list:
    return db.query(Order).order_by(Order.created_at.desc()).all()


def get_unclear_orders(db: Session) -> list:
    return db.query(Order).filter(
        Order.is_unclear.is_(True)
    ).order_by(Order.created_at.desc()).all()


def get_todays_orders(db: Session) -> list:
    today = get_today_ist()
    return db.query(Order).filter(
        func.date(Order.created_at) == today,
        Order.is_cancelled == False,
    ).order_by(Order.created_at.asc()).all()


def get_orders_by_date(db: Session, target_date: date) -> list:
    return db.query(Order).filter(
        func.date(Order.created_at) == target_date,
        Order.is_cancelled == False,
    ).order_by(Order.created_at.asc()).all()


def get_customer_order_history(db: Session, customer_id: int) -> list:
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        return []
    return (
        db.query(Order)
        .filter(Order.customer_phone == customer.phone_number)
        .order_by(Order.created_at.desc())
        .all()
    )
