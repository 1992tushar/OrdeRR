import json
import os
import re
import secrets
from datetime import date, datetime, timezone, timedelta

from sqlalchemy.orm import Session

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
from app.services.template_parser import parse_template_order
from app.services.customer_service import get_customer_by_phone, create_new_customer
from app.services.adhoc_reporter import is_report_keyword, handle_adhoc_report_request
from app.services.intent_classifier import (
    Intent,
    classify_intent,
    CANCEL_KEYWORDS,
    REPEAT_KEYWORDS,
    HISTORY_KEYWORDS,
    CONFIRM_YES_WORDS,
    CONFIRM_NO_WORDS,
    GREETINGS,
    FILLER_PHRASES,
)

MANAGER_PHONE        = os.getenv("MANAGER_PHONE", "")
PLANT_NAME           = os.getenv("PLANT_NAME", "Fluffy")
BASE_URL             = os.getenv("BASE_URL", "")   # e.g. https://orderr.onrender.com
IST                  = timezone(timedelta(hours=5, minutes=30))
RESET_HOUR           = 20  # 8 PM IST
DISPATCH_CUTOFF_HOUR = int(os.getenv("DISPATCH_CUTOFF_HOUR", "9"))


# ── Date helpers ──────────────────────────────────────────────────────────────

def get_today_ist() -> date:
    return datetime.now(IST).date()

def compute_business_date(created_at_utc: datetime) -> date:
    ist_time = created_at_utc.astimezone(IST)
    if ist_time.hour >= RESET_HOUR:
        return (ist_time + timedelta(days=1)).date()
    return ist_time.date()

def get_current_business_date() -> date:
    now_ist = datetime.now(IST)
    if now_ist.hour >= RESET_HOUR:
        return (now_ist + timedelta(days=1)).date()
    return now_ist.date()

def get_current_business_date_str() -> str:
    return get_current_business_date().strftime("%Y-%m-%d")

def get_delivery_date_str() -> str:
    return get_today_ist().strftime("%Y-%m-%d")


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_internal_phones(db: Session) -> set:
    salesperson_phones = {
        sp.phone for sp in
        db.query(Salesperson).filter(Salesperson.active == True).all()
    }
    internal = salesperson_phones
    if MANAGER_PHONE:
        internal = internal | {MANAGER_PHONE}
    return internal


def get_todays_active_order(db: Session, customer_phone: str) -> Order | None:
    business_date_str = get_current_business_date_str()
    return (
        db.query(Order)
        .filter(
            Order.customer_phone == customer_phone,
            Order.business_date  == business_date_str,
            Order.is_cancelled   == False,
            Order.status.notin_(["pending_replace", "pending_repeat"]),
        )
        .order_by(Order.created_at.desc())
        .first()
    )


def get_last_order(db: Session, customer_phone: str) -> Order | None:
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


def _get_pending_repeat(db: Session, customer_phone: str) -> Order | None:
    return (
        db.query(Order)
        .filter(
            Order.customer_phone == customer_phone,
            Order.status         == "pending_repeat",
            Order.delivery_date  == get_delivery_date_str(),
        )
        .order_by(Order.created_at.desc())
        .first()
    )


def _get_pending_replace(db: Session, customer_phone: str) -> Order | None:
    return (
        db.query(Order)
        .filter(
            Order.customer_phone == customer_phone,
            Order.status         == "pending_replace",
            Order.delivery_date  == get_delivery_date_str(),
        )
        .order_by(Order.created_at.desc())
        .first()
    )


# ── Notification helpers ──────────────────────────────────────────────────────

def _build_unclear_alert(
    restaurant_name: str,
    customer_phone: str,
    unclear_items: list,
    parsed_items: list,
) -> str:
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


def _has_digits(text: str) -> bool:
    """Return True if the message contains any digit — signals a quantity/order attempt."""
    return bool(re.search(r'\d', text))


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
        business_date  = get_current_business_date_str(),
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

        # For edits, confirmation is the update message above — treat as sent
        order.confirmation_sent    = True
        order.forwarded_to_manager = True

    else:
        # Capture actual send result — confirmation_sent reflects reality
        confirmation_sent = send_order_confirmation(
            customer_phone  = customer_phone,
            parsed          = parsed,
            restaurant_name = restaurant_name,
        )

        if not unclear_items:
            send_manager_alert(
                manager_phone   = MANAGER_PHONE,
                customer_phone  = customer_phone,
                parsed          = parsed,
                restaurant_name = restaurant_name,
            )
        if unclear_items:
            try:
                send_whatsapp_message(
                    MANAGER_PHONE,
                    _build_unclear_alert(restaurant_name, customer_phone, unclear_items, parsed_items),
                )
            except Exception as e:
                print(f"⚠️ Unclear items manager alert failed: {e}")

        order.confirmation_sent    = confirmation_sent  # True only if WA API succeeded
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


# ── Intent handlers ───────────────────────────────────────────────────────────

def _handle_onboarding(db: Session, customer: Customer, message: str) -> dict:
    customer_phone = customer.phone_number
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


def _handle_cancel(db: Session, customer: Customer) -> dict:
    customer_phone = customer.phone_number
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
        "✅ Your order has been cancelled.\n\nJust send your order anytime to place a new one.",
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


def _handle_repeat(db: Session, customer: Customer) -> dict:
    customer_phone = customer.phone_number
    last = get_last_order(db, customer_phone)
    if not last or not last.parsed_items:
        send_whatsapp_message(
            customer_phone,
            "ℹ️ No previous order found.\n\nJust send your order anytime to place a new one.",
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
        business_date  = get_current_business_date_str(),
        is_unclear     = False,
        status         = "pending_repeat",
    )
    db.add(pending)
    db.commit()

    send_repeat_order_confirmation_request(customer_phone, items)
    return {"order_id": pending.id, "status": "repeat_requested", "parsed": None}


def _handle_history(db: Session, customer: Customer) -> dict:
    """Send customer a WhatsApp link to their personal order history ledger."""
    customer_phone = customer.phone_number

    # Generate token once; reuse on all subsequent requests (same link forever)
    if not customer.ledger_token:
        customer.ledger_token = secrets.token_urlsafe(24)
        db.commit()

    ledger_url = f"{BASE_URL}/ledger/{customer.ledger_token}"

    send_whatsapp_message(
        customer_phone,
        f"📋 *Your Order History — {PLANT_NAME}*\n\n"
        f"Here's your personal order ledger for the last 7 days:\n\n"
        f"🔗 {ledger_url}\n\n"
        f"The link always works for you — feel free to bookmark it.",
    )
    return {"order_id": None, "status": "history_sent", "parsed": None}


def _handle_confirm_yes(db: Session, customer: Customer) -> dict:
    """Customer confirmed a pending repeat or replace."""
    customer_phone    = customer.phone_number
    pending_repeat    = _get_pending_repeat(db, customer_phone)
    pending_replace   = _get_pending_replace(db, customer_phone)

    if pending_repeat:
        items  = json.loads(pending_repeat.parsed_items)
        parsed = {
            "items": items, "unclear_items": [],
            "delivery_date": None, "delivery_time": None,
            "is_unclear": False, "unclear_reason": None,
        }
        pending_repeat.status = "received"
        db.commit()

        confirmation_sent = send_order_confirmation(customer_phone, parsed, customer.restaurant_name)
        send_manager_alert(MANAGER_PHONE, customer_phone, parsed, customer.restaurant_name)
        pending_repeat.confirmation_sent    = confirmation_sent
        pending_repeat.forwarded_to_manager = True
        db.commit()
        return {"order_id": pending_repeat.id, "status": "repeat_confirmed", "parsed": parsed}

    if pending_replace:
        # Cancel the original order
        old_order = (
            db.query(Order)
            .filter(
                Order.customer_phone == customer_phone,
                Order.delivery_date  == get_delivery_date_str(),
                Order.is_cancelled   == False,
                Order.status.notin_(["pending_replace", "pending_repeat"]),
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
            "items": items, "unclear_items": [],
            "delivery_date": None, "delivery_time": pending_replace.delivery_time,
            "is_unclear": False, "unclear_reason": None,
        }
        pending_replace.status = "received"
        db.commit()

        confirmation_sent = send_order_confirmation(customer_phone, parsed, customer.restaurant_name)
        send_manager_alert(MANAGER_PHONE, customer_phone, parsed, customer.restaurant_name)
        pending_replace.confirmation_sent    = confirmation_sent
        pending_replace.forwarded_to_manager = True
        db.commit()
        return {"order_id": pending_replace.id, "status": "replace_confirmed", "parsed": parsed}

    # Stale yes with no pending order — treat as a normal order attempt
    return _handle_order(db, customer, "yes", is_photo=False)


def _handle_confirm_no(db: Session, customer: Customer) -> dict:
    """Customer declined a pending repeat or replace."""
    customer_phone  = customer.phone_number
    pending_repeat  = _get_pending_repeat(db, customer_phone)
    pending_replace = _get_pending_replace(db, customer_phone)

    if pending_repeat:
        pending_repeat.is_cancelled = True
        pending_repeat.cancelled_at = datetime.now(IST)
        pending_repeat.status       = "cancelled"
        db.commit()
        send_whatsapp_message(customer_phone, "No problem! Just send your order anytime.")
        return {"order_id": None, "status": "repeat_cancelled", "parsed": None}

    if pending_replace:
        pending_replace.is_cancelled = True
        pending_replace.cancelled_at = datetime.now(IST)
        pending_replace.status       = "cancelled"
        db.commit()
        send_whatsapp_message(customer_phone, "✅ Kept your original order. No changes made.")
        return {"order_id": None, "status": "replace_cancelled", "parsed": None}

    # Stale no with nothing pending — silently ignore
    return {"order_id": None, "status": "stale_confirmation_ignored", "parsed": None}


def _handle_order(db: Session, customer: Customer, message: str, is_photo: bool) -> dict:
    """Parse the message as an order, handle duplicate/replace flow, and save."""
    customer_phone = customer.phone_number
    parsed = parse_template_order(customer_phone, message, db=db)

    # Nothing parseable at all
    if parsed["is_unclear"] and not parsed.get("unclear_items"):
        if _has_digits(message):
            # Contains digits → likely a failed order attempt, not small talk.
            # Save as unclear order so it surfaces immediately on the dashboard.
            order = Order(
                plant_name     = PLANT_NAME,
                customer_phone = customer_phone,
                customer_name  = customer.restaurant_name,
                raw_message    = message,
                is_unclear     = True,
                unclear_reason = "Contains numbers but no items could be matched",
                business_date  = get_current_business_date_str(),
                delivery_date  = get_delivery_date_str(),
                status         = "received",
            )
            db.add(order)
            db.commit()

            send_whatsapp_message(
                customer_phone,
                "We received your message but couldn't read the items clearly. "
                "Our team will check and confirm shortly. 🙏",
            )
            return {"order_id": order.id, "status": "order_unclear_no_items", "parsed": None}

        else:
            send_whatsapp_message(
                customer_phone,
                "ℹ️ Sorry, I couldn't understand that as an order.\n\n"
                "Please send your order with item names and quantities, for example:\n"
                "_2 paneer, 1 curd, 3 butter_",
            )
            return {"order_id": None, "status": "unclear_message", "parsed": None}

    existing_order = get_todays_active_order(db, customer_phone)

    if existing_order:
        current_time_ist = datetime.now(IST)
        cutoff_time = current_time_ist.replace(
            hour=DISPATCH_CUTOFF_HOUR, minute=0, second=0, microsecond=0
        )

        if current_time_ist < cutoff_time:
            # Before dispatch cutoff — ask customer to confirm replacement
            pending = Order(
                plant_name     = PLANT_NAME,
                customer_name  = customer.restaurant_name,
                customer_phone = customer_phone,
                raw_message    = message,
                parsed_items   = json.dumps(parsed.get("items", [])),
                unclear_items  = json.dumps(parsed.get("unclear_items", [])) if parsed.get("unclear_items") else None,
                delivery_date  = get_delivery_date_str(),
                delivery_time  = parsed.get("delivery_time"),
                business_date  = get_current_business_date_str(),
                is_unclear     = False,
                status         = "pending_replace",
            )
            db.add(pending)
            db.commit()

            send_replace_confirmation_request(
                customer_phone = customer_phone,
                existing_items = json.loads(existing_order.parsed_items) if existing_order.parsed_items else [],
                new_items      = parsed.get("items", []),
            )
            return {"order_id": pending.id, "status": "replace_requested", "parsed": parsed}

        else:
            # After dispatch cutoff — accept as additional order, alert manager
            result = _save_and_notify(db, customer, parsed, message, is_photo=is_photo)
            existing_items = json.loads(existing_order.parsed_items) if existing_order.parsed_items else []
            try:
                send_whatsapp_message(
                    MANAGER_PHONE,
                    f"⚠️ *Additional Order — {PLANT_NAME}*\n\n"
                    f"🏪 {customer.restaurant_name}\n"
                    f"📱 {customer_phone}\n\n"
                    f"*Original order* (placed at {existing_order.created_at.strftime('%I:%M %p')}):\n"
                    + "\n".join(f"• {i['product']} — {i['quantity']} {i['unit']}" for i in existing_items)
                    + f"\n\n*Additional order:*\n"
                    + "\n".join(f"• {i['product']} — {i['quantity']} {i['unit']}" for i in parsed.get("items", []))
                    + "\n\nPlease check if this can be fulfilled.",
                )
            except Exception:
                pass
            return result

    # No existing order — straightforward save
    return _save_and_notify(db, customer, parsed, message, is_photo=is_photo)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def process_incoming_order(
    db: Session,
    customer_phone: str,
    message: str,
    is_photo: bool = False,
    business_date: str | None = None,
    is_next_day_override: bool = False,
) -> dict:
    if business_date is None:
        business_date = compute_business_date(datetime.now(timezone.utc)).strftime("%Y-%m-%d")

    msg_lower = message.strip().lower()

    # ── 1. Customer lookup / first-time registration ──────────────────────────
    customer = get_customer_by_phone(db, customer_phone)

    if not customer:
        internal_phones = get_internal_phones(db)
        if customer_phone in internal_phones:
            print(f"ℹ️ Ignored message from internal phone {customer_phone}")
            return {"order_id": None, "status": "internal_phone_ignored", "parsed": None}

        customer = create_new_customer(db, customer_phone)
        send_whatsapp_message(
            customer_phone,
            f"👋 Welcome to *{PLANT_NAME}* Ordering System!\n\n"
            "Please reply with your *restaurant or hotel name* to continue.",
        )
        return {"order_id": None, "status": "awaiting_restaurant_name", "parsed": None}

    # ── 2. Classify intent ────────────────────────────────────────────────────
    pending_repeat  = _get_pending_repeat(db, customer_phone)
    pending_replace = _get_pending_replace(db, customer_phone)

    intent = classify_intent(
        message,
        onboarding          = (customer.onboarding_status == "awaiting_name"),
        has_pending_repeat  = pending_repeat is not None,
        has_pending_replace = pending_replace is not None,
    )

    # ── 3. Dispatch ───────────────────────────────────────────────────────────
    if intent == Intent.ONBOARDING:
        return _handle_onboarding(db, customer, message)

    if intent == Intent.CANCEL:
        return _handle_cancel(db, customer)

    if intent == Intent.REPEAT_LAST:
        return _handle_repeat(db, customer)

    if intent == Intent.HISTORY:
        return _handle_history(db, customer)

    if intent == Intent.CONFIRM_YES:
        return _handle_confirm_yes(db, customer)

    if intent == Intent.CONFIRM_NO:
        return _handle_confirm_no(db, customer)

    # Intent.ORDER — default
    return _handle_order(db, customer, message, is_photo=is_photo)


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_all_orders(db: Session) -> list:
    return db.query(Order).order_by(Order.created_at.desc()).all()


def get_unclear_orders(db: Session) -> list:
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
    today_str = get_current_business_date_str()
    return (
        db.query(Order)
        .filter(Order.business_date == today_str, Order.is_cancelled == False)
        .order_by(Order.created_at)
        .all()
    )


def get_orders_by_date(db: Session, target_date: date) -> list:
    return (
        db.query(Order)
        .filter(
            Order.business_date == target_date.strftime("%Y-%m-%d"),
            Order.is_cancelled == False,
        )
        .order_by(Order.created_at)
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


# ── Validation ────────────────────────────────────────────────────────────────

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