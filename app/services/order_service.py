import json
import os
import re
from datetime import date, datetime, timezone, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.order import Order
from app.models.customer import Customer
from app.services.notifier import (
    send_order_confirmation,
    send_manager_alert,
    send_unclear_order_alert,
    send_whatsapp_message,
    send_replace_confirmation_request,
    send_repeat_order_confirmation_request,
)
from app.services.template_parser import parse_template_order, build_error_message
from app.services.customer_service import get_customer_by_phone, create_new_customer
from app.services.adhoc_reporter import is_report_keyword, handle_adhoc_report_request

MANAGER_PHONE        = os.getenv("MANAGER_PHONE", "")
PLANT_NAME           = os.getenv("PLANT_NAME", "Fluffy")
IST                  = timezone(timedelta(hours=5, minutes=30))
DISPATCH_CUTOFF_HOUR = int(os.getenv("DISPATCH_CUTOFF_HOUR", "9"))


# ── Keyword sets ──────────────────────────────────────────────────────────────

CANCEL_KEYWORDS = {
    "cancel", "cancel order", "cancel my order", "order cancel",
    "cancel karo", "cancel kar", "band karo", "mat bhejo",
    "order band", "no order", "no order today", "aaj nahi",
    "order nahi", "nahi chahiye",
}

MENU_KEYWORDS = {
    "menu", "order", "show menu", "send menu", "place order",
    "kya hai", "product", "list", "rate", "rate list",
}

REPEAT_KEYWORDS = {
    "same", "repeat", "same order", "repeat order",
    "same as yesterday", "same as last time",
    "wahi bhejo", "wahi order", "same bhejo",
}

CONFIRM_YES = {"yes", "haan", "ha", "haa", "ok", "okay", "confirm", "okk"}
CONFIRM_NO  = {"no", "nahi", "nope", "cancel", "don't", "dont"}

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
    return get_today_ist().strftime("%Y-%m-%d")


def get_todays_active_order(db: Session, customer_phone: str) -> Order | None:
    today_str = get_today_ist().strftime("%Y-%m-%d")
    return (
        db.query(Order)
        .filter(
            Order.customer_phone == customer_phone,
            Order.delivery_date  == today_str,
            Order.is_cancelled   == False,
            Order.status.notin_(["pending_replace", "pending_repeat"]),
        )
        .order_by(Order.created_at.desc())
        .first()
    )


def get_last_order(db: Session, customer_phone: str) -> Order | None:
    """Most recent completed (non-cancelled, non-unclear) order ever."""
    return (
        db.query(Order)
        .filter(
            Order.customer_phone == customer_phone,
            Order.is_cancelled   == False,
            Order.is_unclear     == False,
        )
        .order_by(Order.created_at.desc())
        .first()
    )


def _save_and_notify(
    db: Session,
    customer: Customer,
    parsed: dict,
    raw_message: str,
    is_photo: bool = False,
    is_edit: bool = False,
) -> dict:
    """
    Central save + notify. Called from multiple paths.
    """
    customer_phone  = customer.phone_number
    restaurant_name = customer.restaurant_name

    order = Order(
        plant_name     = PLANT_NAME,
        customer_name  = restaurant_name,
        customer_phone = customer_phone,
        raw_message    = raw_message,
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

    if parsed.get("is_unclear"):
        send_unclear_order_alert(
            manager_phone  = MANAGER_PHONE,
            customer_phone = customer_phone,
            raw_message    = raw_message,
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
        send_order_confirmation(
            customer_phone  = customer_phone,
            parsed          = parsed,
            restaurant_name = restaurant_name,
        )
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

    # ── 0. Ad hoc report request (manager / salesperson only) ─────────────────
    # Check before any customer logic — manager/salesperson are not customers.
    # Returns True if handled (sends report + returns early).
    # Returns False if unknown phone — falls through to normal pipeline.
    if is_report_keyword(msg_lower):
        handled = handle_adhoc_report_request(customer_phone, msg_lower, db)
        if handled:
            return {"order_id": None, "status": "adhoc_report_sent", "parsed": None}
        # Unknown phone with a report keyword → fall through to order pipeline
        # (words like "today"/"pending" produce no product matches → unclear response)

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

        from app.services.product_catalog import generate_order_template
        send_whatsapp_message(
            customer_phone,
            f"✅ Welcome *{customer.restaurant_name}*!\n\n"
            f"To place your order:\n\n"
            f"1️⃣ Copy the template below\n"
            f"2️⃣ Fill in your quantities\n"
            f"3️⃣ Delete items you don't need\n"
            f"4️⃣ Send it back\n\n"
            f"👇 *Your order template:*\n\n"
            f"{generate_order_template()}",
        )

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

    # ── 3. Menu / order template trigger ─────────────────────────────────────
    if msg_lower in MENU_KEYWORDS:
        from app.services.product_catalog import generate_order_template
        send_whatsapp_message(customer_phone, generate_order_template())
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
            f"✅ Your order has been cancelled.\n\n"
            f"To place a new order, just type *order* anytime.",
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

    # ── 5. Repeat last order ──────────────────────────────────────────────────
    if msg_lower in REPEAT_KEYWORDS:
        last = get_last_order(db, customer_phone)
        if not last or not last.parsed_items:
            send_whatsapp_message(
                customer_phone,
                f"ℹ️ No previous order found.\n\nType *order* to place a new one.",
            )
            return {"order_id": None, "status": "no_last_order", "parsed": None}

        items = json.loads(last.parsed_items)
        pending = Order(
            plant_name     = PLANT_NAME,
            customer_name  = customer.restaurant_name,
            customer_phone = customer_phone,
            raw_message    = "repeat",
            parsed_items   = json.dumps(items),
            delivery_date  = get_delivery_date_str(),
            is_unclear     = False,
            status         = "pending_repeat",
        )
        db.add(pending)
        db.commit()

        send_repeat_order_confirmation_request(customer_phone, items)
        return {"order_id": pending.id, "status": "repeat_requested", "parsed": None}

    # ── 6. Handle yes/no replies ──────────────────────────────────────────────
    if msg_lower in CONFIRM_YES or msg_lower in CONFIRM_NO:

        # Check for pending_repeat
        pending_repeat = (
            db.query(Order)
            .filter(
                Order.customer_phone == customer_phone,
                Order.status         == "pending_repeat",
                Order.delivery_date  == get_delivery_date_str(),
            )
            .order_by(Order.created_at.desc())
            .first()
        )

        if pending_repeat:
            if msg_lower in CONFIRM_NO:
                pending_repeat.is_cancelled = True
                pending_repeat.cancelled_at = datetime.now(IST)
                pending_repeat.status       = "cancelled"
                db.commit()
                from app.services.product_catalog import generate_order_template
                send_whatsapp_message(
                    customer_phone,
                    f"No problem! Type *order* to place a fresh order.\n\n"
                    f"{generate_order_template()}",
                )
                return {"order_id": None, "status": "repeat_cancelled", "parsed": None}

            # Yes — confirm the repeat
            items  = json.loads(pending_repeat.parsed_items)
            parsed = {
                "items":          items,
                "delivery_date":  None,
                "delivery_time":  None,
                "is_unclear":     False,
                "unclear_reason": None,
            }
            pending_repeat.status = "received"
            db.commit()

            send_order_confirmation(
                customer_phone  = customer_phone,
                parsed          = parsed,
                restaurant_name = customer.restaurant_name,
            )
            send_manager_alert(
                manager_phone   = MANAGER_PHONE,
                customer_phone  = customer_phone,
                parsed          = parsed,
                restaurant_name = customer.restaurant_name,
            )
            pending_repeat.confirmation_sent    = True
            pending_repeat.forwarded_to_manager = True
            db.commit()

            return {"order_id": pending_repeat.id, "status": "repeat_confirmed", "parsed": parsed}

        # Check for pending_replace
        pending_replace = (
            db.query(Order)
            .filter(
                Order.customer_phone == customer_phone,
                Order.status         == "pending_replace",
                Order.delivery_date  == get_delivery_date_str(),
            )
            .order_by(Order.created_at.desc())
            .first()
        )

        if pending_replace:
            if msg_lower in CONFIRM_NO:
                pending_replace.is_cancelled = True
                pending_replace.cancelled_at = datetime.now(IST)
                pending_replace.status       = "cancelled"
                db.commit()
                send_whatsapp_message(
                    customer_phone,
                    "✅ Kept your original order. No changes made.",
                )
                return {"order_id": None, "status": "replace_cancelled", "parsed": None}

            # Yes — cancel old order and confirm new one
            old_order = (
                db.query(Order)
                .filter(
                    Order.customer_phone == customer_phone,
                    Order.delivery_date  == get_delivery_date_str(),
                    Order.is_cancelled   == False,
                    Order.status         != "pending_replace",
                    Order.status         != "pending_repeat",
                )
                .order_by(Order.created_at.asc())
                .first()
            )
            if old_order:
                old_order.is_cancelled = True
                old_order.cancelled_at = datetime.now(IST)
                old_order.status       = "cancelled"

            items  = json.loads(pending_replace.parsed_items)
            parsed = {
                "items":          items,
                "delivery_date":  None,
                "delivery_time":  pending_replace.delivery_time,
                "is_unclear":     False,
                "unclear_reason": None,
            }
            pending_replace.status = "received"
            db.commit()

            send_order_confirmation(
                customer_phone  = customer_phone,
                parsed          = parsed,
                restaurant_name = customer.restaurant_name,
            )
            send_manager_alert(
                manager_phone   = MANAGER_PHONE,
                customer_phone  = customer_phone,
                parsed          = parsed,
                restaurant_name = customer.restaurant_name,
            )
            pending_replace.confirmation_sent    = True
            pending_replace.forwarded_to_manager = True
            db.commit()

            return {"order_id": pending_replace.id, "status": "replace_confirmed", "parsed": parsed}

    # ── 7. Parse order ────────────────────────────────────────────────────────
    parsed = parse_template_order(customer_phone, message)

    # If completely unclear — send template + instruction
    if parsed["is_unclear"]:
        from app.services.product_catalog import generate_order_template
        send_whatsapp_message(
            customer_phone,
            f"ℹ️ I couldn't read that as an order.\n\n"
            f"Please use this template:\n\n"
            f"{generate_order_template()}",
        )
        return {"order_id": None, "status": "unclear_message", "parsed": None}

    # Partial errors — accept good items, flag bad ones
    errors = parsed.get("errors", [])

    # ── 8. Duplicate / replace flow ───────────────────────────────────────────
    existing_order = get_todays_active_order(db, customer_phone)

    if existing_order:
        current_time_ist = datetime.now(IST)
        cutoff_time = current_time_ist.replace(hour=DISPATCH_CUTOFF_HOUR, minute=0, second=0, microsecond=0)

        if current_time_ist < cutoff_time:
            # Before 9 AM — customer is amending, ask to replace
            existing_items = json.loads(existing_order.parsed_items) if existing_order.parsed_items else []

            pending = Order(
                plant_name     = PLANT_NAME,
                customer_name  = customer.restaurant_name,
                customer_phone = customer_phone,
                raw_message    = message,
                parsed_items   = json.dumps(parsed.get("items", [])),
                delivery_date  = get_delivery_date_str(),
                delivery_time  = parsed.get("delivery_time"),
                is_unclear     = False,
                status         = "pending_replace",
            )
            db.add(pending)
            db.commit()

            send_replace_confirmation_request(
                customer_phone = customer_phone,
                existing_items = existing_items,
                new_items      = parsed.get("items", []),
            )
            return {"order_id": pending.id, "status": "replace_requested", "parsed": parsed}

        else:
            # After 9 AM — post-dispatch, treat as fresh additional order
            result = _save_and_notify(
                db          = db,
                customer    = customer,
                parsed      = parsed,
                raw_message = message,
                is_photo    = is_photo,
                is_edit     = False,
            )

            existing_items = json.loads(existing_order.parsed_items) if existing_order.parsed_items else []
            existing_items_text = "\n".join(
                f"• {i['product']} — {i['quantity']} {i['unit']}"
                for i in existing_items
            )
            new_items_text = "\n".join(
                f"• {i['product']} — {i['quantity']} {i['unit']}"
                for i in parsed.get("items", [])
            )
            try:
                send_whatsapp_message(
                    MANAGER_PHONE,
                    f"⚠️ *Additional Order — {PLANT_NAME}*\n\n"
                    f"🏪 {customer.restaurant_name}\n"
                    f"📱 {customer_phone}\n\n"
                    f"*Original order* (placed at {existing_order.created_at.strftime('%I:%M %p')}):\n"
                    f"{existing_items_text}\n\n"
                    f"*Additional order:*\n"
                    f"{new_items_text}\n\n"
                    f"Please check if this can be fulfilled.",
                )
            except Exception:
                pass

            return result

    # ── 9. Save order ─────────────────────────────────────────────────────────
    result = _save_and_notify(
        db          = db,
        customer    = customer,
        parsed      = parsed,
        raw_message = message,
        is_photo    = is_photo,
        is_edit     = False,
    )

    # Send partial error feedback after confirmation if needed
    if errors:
        send_whatsapp_message(customer_phone, build_error_message(errors))

    return result


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_all_orders(db: Session) -> list:
    return db.query(Order).order_by(Order.created_at.desc()).all()


def get_unclear_orders(db: Session) -> list:
    return (
        db.query(Order)
        .filter(Order.is_unclear.is_(True))
        .order_by(Order.created_at.desc())
        .all()
    )


def get_todays_orders(db: Session) -> list:
    today = get_today_ist()
    return (
        db.query(Order)
        .filter(
            func.date(Order.created_at) == today,
            Order.is_cancelled == False,
        )
        .order_by(Order.created_at.asc())
        .all()
    )


def get_orders_by_date(db: Session, target_date: date) -> list:
    return (
        db.query(Order)
        .filter(
            func.date(Order.created_at) == target_date,
            Order.is_cancelled == False,
        )
        .order_by(Order.created_at.asc())
        .all()
    )


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