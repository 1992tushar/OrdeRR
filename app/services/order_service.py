import json
import os
import re
from datetime import date, datetime, timezone, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.order import Order
from app.models.customer import Customer
from app.models.salesperson import Salesperson
from app.services.notifier import (
    send_order_confirmation,
    send_manager_alert,
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


def get_internal_phones(db: Session) -> set:
    """
    Returns a set of phones that belong to internal staff (manager + active salespersons).
    These should never be registered as customers.
    """
    salesperson_phones = {
        sp.phone for sp in
        db.query(Salesperson).filter(Salesperson.active == True).all()
    }
    internal = salesperson_phones
    if MANAGER_PHONE:
        internal = internal | {MANAGER_PHONE}
    return internal


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


def _build_unclear_alert(restaurant_name: str, customer_phone: str, unclear_items: list, parsed_items: list) -> str:
    """Build a WhatsApp message alerting manager about unclear items in an order."""
    lines = [
        f"⚠️ *Unclear Items in Order — {PLANT_NAME}*\n",
        f"🏪 {restaurant_name}",
        f"📱 {customer_phone}\n",
    ]
    if parsed_items:
        lines.append("*Parsed items (confirmed):*")
        for i in parsed_items:
            qty = int(i['quantity']) if i['quantity'] == int(i['quantity']) else i['quantity']
            lines.append(f"  ✅ {i['product']} — {qty} {i['unit']}")
        lines.append("")

    lines.append("*Could not understand:*")
    for raw in unclear_items:
        lines.append(f"  ❓ {raw}")

    lines.append("\nPlease review on the dashboard and assign correct product names.")
    return "\n".join(lines)


def _save_and_notify(
    db: Session,
    customer: Customer,
    parsed: dict,
    raw_message: str,
    is_photo: bool = False,
    is_edit: bool = False,
) -> dict:
    customer_phone  = customer.phone_number
    restaurant_name = customer.restaurant_name
    unclear_items   = parsed.get("unclear_items", [])
    parsed_items    = parsed.get("items", [])

    order = Order(
        plant_name     = PLANT_NAME,
        customer_name  = restaurant_name,
        customer_phone = customer_phone,
        raw_message    = raw_message,
        is_photo_order = is_photo,
        parsed_items   = json.dumps(parsed_items),
        unclear_items  = json.dumps(unclear_items) if unclear_items else None,
        delivery_date  = get_delivery_date_str(),
        delivery_time  = parsed.get("delivery_time"),
        is_unclear     = parsed.get("is_unclear", False),
        unclear_reason = parsed.get("unclear_reason"),
        status         = "received",
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    if is_edit:
        items_text = "\n".join(
            f"• {i['product']} — {int(i['quantity']) if i['quantity'] == int(i['quantity']) else i['quantity']} {i['unit']}"
            for i in parsed_items
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
        if unclear_items:
            try:
                alert_text = _build_unclear_alert(
                    restaurant_name = restaurant_name,
                    customer_phone  = customer_phone,
                    unclear_items   = unclear_items,
                    parsed_items    = parsed_items,
                )
                send_whatsapp_message(MANAGER_PHONE, alert_text)
            except Exception as e:
                print(f"⚠️ Unclear items manager alert failed: {e}")

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


def _is_greeting_or_filler(msg_lower: str) -> bool:
    """Returns True if the message is a known greeting or filler phrase."""
    if msg_lower in GREETINGS:
        return True
    if msg_lower in FILLER_PHRASES:
        return True
    # Single word fillers
    single_word_fillers = {
        "yes", "no", "ok", "okay", "sure", "fine", "please", "pls",
        "hi", "hello", "hey", "thanks", "thank", "haan", "nahi",
        "ji", "ha", "bhai", "yep", "yup", "nope", "good", "great",
    }
    words = msg_lower.split()
    if len(words) >= 2 and all(w in single_word_fillers for w in words):
        return True
    return False


# ── Main pipeline ─────────────────────────────────────────────────────────────

def process_incoming_order(
    db: Session,
    customer_phone: str,
    message: str,
    is_photo: bool = False,
) -> dict:

    msg_lower = message.strip().lower()

    # ── 0. Ad hoc report request (manager / salesperson only) ─────────────────
    if is_report_keyword(msg_lower):
        handled = handle_adhoc_report_request(customer_phone, msg_lower, db)
        if handled:
            return {"order_id": None, "status": "adhoc_report_sent", "parsed": None}

    # ── 1. Lookup / create customer ───────────────────────────────────────────
    customer = get_customer_by_phone(db, customer_phone)

    if not customer:
        internal_phones = get_internal_phones(db)
        if customer_phone in internal_phones:
            print(f"ℹ️ Ignored message from internal phone {customer_phone} — not a customer")
            return {"order_id": None, "status": "internal_phone_ignored", "parsed": None}

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
            f"✅ *Welcome, {customer.restaurant_name}!*\n\n"
            f"You're all set. Just send your order anytime — "
            f"list the items and quantities in your own way and we'll take care of it. 🙌",
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

    # ── 3. Cancel order ───────────────────────────────────────────────────────
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
            f"Just send your order anytime to place a new one.",
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

    # ── 4. Repeat last order ──────────────────────────────────────────────────
    if msg_lower in REPEAT_KEYWORDS:
        last = get_last_order(db, customer_phone)
        if not last or not last.parsed_items:
            send_whatsapp_message(
                customer_phone,
                f"ℹ️ No previous order found.\n\nJust send your order anytime to place a new one.",
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

    # ── 5. Handle yes/no replies ──────────────────────────────────────────────
    if msg_lower in CONFIRM_YES or msg_lower in CONFIRM_NO:

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
                send_whatsapp_message(
                    customer_phone,
                    f"No problem! Just send your order anytime.",
                )
                return {"order_id": None, "status": "repeat_cancelled", "parsed": None}

            items  = json.loads(pending_repeat.parsed_items)
            parsed = {
                "items":          items,
                "unclear_items":  [],
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
                "unclear_items":  [],
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

    # ── 6. Parse order ────────────────────────────────────────────────────────
    parsed = parse_template_order(customer_phone, message, db=db)

    # Zero items parsed — could be a greeting, filler, or a customer note
    if parsed["is_unclear"] and not parsed.get("unclear_items"):

        # ── 6a. Greeting / filler — acknowledge warmly, silent drop ──────────
        if _is_greeting_or_filler(msg_lower):
            send_whatsapp_message(customer_phone, "😊 Anytime!")
            return {"order_id": None, "status": "greeting_ignored", "parsed": None}

        # ── 6b. Customer note — acknowledge + store for daily report ──────────
        # The raw message is already persisted in inbound_messages with status NOTE.
        # reporter.py picks up all NOTE messages when generating the daily report.
        send_whatsapp_message(
            customer_phone,
            "Noted! We'll pass it on. 😊",
        )
        return {"order_id": None, "status": "customer_note_received", "parsed": None}

    # ── 7. Duplicate / replace flow ───────────────────────────────────────────
    existing_order = get_todays_active_order(db, customer_phone)

    if existing_order:
        current_time_ist = datetime.now(IST)
        cutoff_time = current_time_ist.replace(
            hour=DISPATCH_CUTOFF_HOUR, minute=0, second=0, microsecond=0
        )

        if current_time_ist < cutoff_time:
            existing_items = json.loads(existing_order.parsed_items) if existing_order.parsed_items else []
            pending = Order(
                plant_name     = PLANT_NAME,
                customer_name  = customer.restaurant_name,
                customer_phone = customer_phone,
                raw_message    = message,
                parsed_items   = json.dumps(parsed.get("items", [])),
                unclear_items  = json.dumps(parsed.get("unclear_items", [])) if parsed.get("unclear_items") else None,
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

    # ── 8. Save order ─────────────────────────────────────────────────────────
    return _save_and_notify(
        db          = db,
        customer    = customer,
        parsed      = parsed,
        raw_message = message,
        is_photo    = is_photo,
        is_edit     = False,
    )


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_all_orders(db: Session) -> list:
    return db.query(Order).order_by(Order.created_at.desc()).all()


def get_unclear_orders(db: Session) -> list:
    """Orders that have any unclear_items (partial or total)."""
    return (
        db.query(Order)
        .filter(
            Order.unclear_items.isnot(None),
            Order.unclear_items != "[]",
            Order.unclear_items != "null",
            Order.is_cancelled == False,
        )
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