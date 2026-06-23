import json
import logging
import os
import hmac
import hashlib

from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session
from orderr_core.services.customer_service import create_customer_manually
from orderr_core.database import get_db
from orderr_core.services.order_service import process_incoming_order
from orderr_core.services.reporter import send_daily_report
from orderr_core.services.product_catalog import generate_menu_template
from orderr_core.services.notifier import send_whatsapp_message
from orderr_core.services.customer_service import normalize_phone

from collections import defaultdict
import time

from orderr_core.services.message_journal import (
    persist_raw_message,
    send_acknowledgement,
    record_failure,
    record_manual_review,
    transition,
    get_reliability_stats,
    get_all_failed_messages,
    get_messages_for_manual_review,
)
from orderr_core.auth import require_auth

logger = logging.getLogger(__name__)
router = APIRouter()
MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
ADD_CUSTOMER_CMD = "add customer"

# Statuses returned by process_incoming_order() that are NOT real orders.
NON_ORDER_STATUSES = {
    "awaiting_restaurant_name",
    "invalid_restaurant_name",
    "customer_onboarded",
    "menu_sent",
    "unclear_message",
    "no_order_to_cancel",
    "order_cancelled",
    "no_last_order",
    "repeat_requested",
    "replace_requested",
    "repeat_cancelled",
    "replace_cancelled",
    "repeat_confirmed",
    "replace_confirmed",
    "greeting_ignored",
    "customer_note_received",
    "history_sent", 
}


# Simple in-memory rate limiter: max 10 messages per phone per 60 seconds
_rate_limit: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60  # seconds

def _is_rate_limited(phone: str) -> bool:
    now = time.time()
    timestamps = _rate_limit[phone]
    _rate_limit[phone] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limit[phone]) >= RATE_LIMIT_MAX:
        return True
    _rate_limit[phone].append(now)
    return False


def verify_meta_signature(body: bytes, signature_header: str) -> bool:
    app_secret = os.getenv("META_APP_SECRET", "")
    if not app_secret:
        return True
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    received = signature_header[len("sha256="):]
    return hmac.compare_digest(expected, received)


@router.get("/health")
def health_check():
    return {"status": "OrdeRR webhook is running"}


@router.post("/test")
async def test_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        payload        = await request.json()
        customer_phone = payload.get("phone", "919999999999")
        message_text   = payload.get("message", "")
        if not message_text:
            return {"status": "error", "message": "No message provided"}

        normalized_sender  = normalize_phone(customer_phone)
        normalized_manager = normalize_phone(MANAGER_PHONE) if MANAGER_PHONE else ""

        if (
            normalized_manager
            and normalized_sender == normalized_manager
            and message_text.strip().lower().startswith(ADD_CUSTOMER_CMD)
        ):
            reply = handle_manager_add_customer(db, message_text)
            send_whatsapp_message(customer_phone, reply)
            return {"status": "manager_command_executed", "reply": reply}

        result = process_incoming_order(db=db, customer_phone=customer_phone, message=message_text)
        return {
            "status"    : "success",
            "order_id"  : result.get("order_id"),
            "customer"  : customer_phone,
            "parsed"    : result.get("parsed"),
            "is_unclear": result.get("parsed", {}).get("is_unclear", False) if result.get("parsed") else False,
        }
    except Exception as e:
        logger.exception("Test webhook error")
        return {"status": "error", "message": str(e)}


@router.post("/report")
def trigger_report(
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    send_daily_report(db)
    return {"status": "Daily report sent"}


@router.get("/meta")
async def meta_webhook_verify(request: Request):
    mode      = request.query_params.get("hub.mode")
    token     = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if not mode:
        raise HTTPException(status_code=400, detail="Missing hub.mode")
    if mode == "subscribe" and token == os.getenv("META_VERIFY_TOKEN", ""):
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content=str(challenge))
    raise HTTPException(status_code=403, detail="Invalid verify token")


def handle_manager_add_customer(db: Session, message_text: str) -> str:
    """
    Parse: ADD CUSTOMER <phone> <restaurant name>
    Returns a reply string to send back to manager.
    """
    parts = message_text.strip()[len(ADD_CUSTOMER_CMD):].strip()
    tokens = parts.split(None, 1)
    if len(tokens) < 2:
        return (
            "⚠️ Format: ADD CUSTOMER <phone> <restaurant name>\n"
            "Example: ADD CUSTOMER 9876543210 Hotel Delicious"
        )

    phone_raw, restaurant_name = tokens[0], tokens[1]

    try:
        customer = create_customer_manually(
            db=db,
            phone=phone_raw,
            restaurant_name=restaurant_name,
        )
        return (
            f"✅ Customer added!\n"
            f"🏪 {customer.restaurant_name}\n"
            f"📞 {customer.phone_number}"
        )
    except ValueError as e:
        return f"⚠️ {str(e)}"
    except Exception as e:
        return f"❌ Failed to add customer: {str(e)}"


def _get_internal_phones(db: Session) -> dict[str, str]:
    """
    Returns a dict of {normalized_phone: role} for all internal phones.
    role is "manager" or "salesperson".
    Used to quickly identify internal senders before customer lookup.
    """
    from orderr_core.models.salesperson import Salesperson
    result = {}
    if MANAGER_PHONE:
        result[normalize_phone(MANAGER_PHONE)] = "manager"
    for sp in db.query(Salesperson).filter(Salesperson.active == True).all():
        result[normalize_phone(sp.phone)] = "salesperson"
    return result


@router.post("/meta")
async def meta_webhook(request: Request, db: Session = Depends(get_db)):
    body      = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_meta_signature(body, signature):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except Exception as e:
        logger.error("Could not parse webhook body: %s", e)
        return {"status": "ok"}

    payload_str = body.decode("utf-8", errors="replace")

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value    = change.get("value", {})
            messages = value.get("messages", [])

            for message in messages:
                customer_phone  = message.get("from", "")
                if _is_rate_limited(customer_phone):
                    logger.warning(f"Rate limit hit for {customer_phone} — skipping message")
                    continue

                message_type    = message.get("type", "")
                meta_message_id = message.get("id")

                # ── Extract raw_message based on type ─────────────────────────
                if message_type == "text":
                    raw_message = message.get("text", {}).get("body", "")
                elif message_type == "image":
                    raw_message = message.get("image", {}).get("caption", "Photo order received")
                elif message_type == "interactive":
                    # Button reply: extract button ID as raw_message
                    interactive = message.get("interactive", {})
                    if interactive.get("type") == "button_reply":
                        button_reply = interactive.get("button_reply", {})
                        raw_message  = button_reply.get("id", "")
                    else:
                        raw_message = f"[interactive:{interactive.get('type','unknown')}]"
                else:
                    raw_message = f"[{message_type} message]"

                if not customer_phone:
                    continue

                # ── Persist raw message ───────────────────────────────────────
                inbound_msg = persist_raw_message(
                    db              = db,
                    meta_message_id = meta_message_id,
                    customer_phone  = customer_phone,
                    raw_message     = raw_message,
                    payload_json    = payload_str,
                    message_type    = message_type,
                )

                if inbound_msg is None:
                    logger.critical("Could not persist message from %s — skipping", customer_phone)
                    continue

                # ── Idempotency ───────────────────────────────────────────────
                if inbound_msg.is_duplicate:
                    logger.info("Duplicate webhook meta_id=%s — safe skip", meta_message_id)
                    continue

                # ── Handle interactive button replies ─────────────────────────
                if message_type == "interactive":
                    from orderr_core.services.adhoc_reporter import is_button_reply_id, handle_button_reply
                    button_id = raw_message  # already extracted above
                    if is_button_reply_id(button_id):
                        try:
                            handled = handle_button_reply(customer_phone, button_id, db)
                            if handled:
                                transition(inbound_msg, "CONFIRMED", db)
                                logger.info(
                                    "Button reply handled: phone=%s button=%s",
                                    customer_phone, button_id
                                )
                            else:
                                logger.info(
                                    "Button reply from unknown sender %s — ignoring",
                                    customer_phone
                                )
                                transition(inbound_msg, "CONFIRMED", db)
                        except Exception as e:
                            record_failure(db, inbound_msg, f"Button reply failed: {e}")
                    else:
                        # Unknown interactive type — just confirm and move on
                        transition(inbound_msg, "CONFIRMED", db)
                    continue

                # ── Photo message ─────────────────────────────────────────────
                if message_type == "image":
                    try:
                        send_whatsapp_message(
                            customer_phone,
                            "📸 Sorry, we cannot process photo orders.\n\n"
                            "Please type your order with item names and quantities, for example:\n"
                            "_2 paneer, 1 curd, 3 butter_"
                        )
                        transition(inbound_msg, "CONFIRMED", db)
                    except Exception as e:
                        record_failure(db, inbound_msg, f"Photo reply failed: {e}")
                    continue

                # ── Unsupported type ──────────────────────────────────────────
                if message_type != "text":
                    logger.info("Unsupported type %s from %s — persisted only", message_type, customer_phone)
                    continue

                if not raw_message:
                    continue

                # ── Manager ADD CUSTOMER command ──────────────────────────────
                normalized_sender  = normalize_phone(customer_phone)
                normalized_manager = normalize_phone(MANAGER_PHONE) if MANAGER_PHONE else ""

                if (
                    normalized_manager
                    and normalized_sender == normalized_manager
                    and raw_message.strip().lower().startswith(ADD_CUSTOMER_CMD)
                ):
                    try:
                        reply = handle_manager_add_customer(db, raw_message)
                        send_whatsapp_message(customer_phone, reply)
                        transition(inbound_msg, "CONFIRMED", db)
                    except Exception as e:
                        record_failure(db, inbound_msg, f"Add customer failed: {e}")
                    continue

                # ── Check if internal phone (manager/salesperson) ─────────────
                # For internal phones: report keywords go to adhoc reporter,
                # anything else gets the interactive menu.
                internal_phones = _get_internal_phones(db)
                if normalized_sender in internal_phones:
                    from orderr_core.services.adhoc_reporter import (
                        is_report_keyword,
                        handle_adhoc_report_request,
                        handle_unrecognized_internal_message,
                    )
                    msg_lower = raw_message.strip().lower()
                    try:
                        if is_report_keyword(msg_lower):
                            handle_adhoc_report_request(customer_phone, msg_lower, db)
                        else:
                            handle_unrecognized_internal_message(customer_phone, db)
                        transition(inbound_msg, "CONFIRMED", db)
                    except Exception as e:
                        record_failure(db, inbound_msg, f"Internal message handling failed: {e}")
                    continue

                # ── Customer flow from here ───────────────────────────────────

                # Pre-check: is this customer new or mid-onboarding?
                from orderr_core.models.customer import Customer as CustomerModel
                existing_customer = (
                    db.query(CustomerModel)
                    .filter(CustomerModel.phone_number == customer_phone)
                    .first()
                )
                is_onboarding = (
                    existing_customer is None
                    or getattr(existing_customer, "onboarding_status", None) == "awaiting_name"
                )

                # Menu trigger — onboarding / new customers only
                msg_lower = raw_message.strip().lower()
                MENU_TRIGGER = {"menu", "order", "show menu", "send menu", "place order"}
                if is_onboarding and msg_lower in MENU_TRIGGER:
                    try:
                        send_whatsapp_message(customer_phone, generate_menu_template())
                        transition(inbound_msg, "CONFIRMED", db)
                    except Exception as e:
                        record_failure(db, inbound_msg, f"Menu send failed: {e}")
                    continue

                # ── Parse as order ────────────────────────────────────────────
                transition(inbound_msg, "PARSING", db)

                try:
                    result = process_incoming_order(
                        db             = db,
                        customer_phone = customer_phone,
                        message        = raw_message,
                        is_photo       = (message_type == "image"),
                    )

                    result_status = result.get("status", "")

                    if result.get("order_id"):
                        inbound_msg.linked_order_id = result["order_id"]

                    if result_status == "customer_note_received":
                        transition(inbound_msg, "NOTE", db)
                    elif result_status not in NON_ORDER_STATUSES:
                        transition(inbound_msg, "CONFIRMED", db)

                    db.commit()

                    logger.info(
                        "msg id=%s → order_id=%s status=%s",
                        inbound_msg.id,
                        result.get("order_id"),
                        result_status,
                    )

                except Exception as e:
                    logger.exception(
                        "Processing failed for msg id=%s phone=%s",
                        inbound_msg.id,
                        customer_phone,
                    )
                    record_failure(
                        db,
                        inbound_msg,
                        reason=f"{type(e).__name__}: {str(e)[:300]}"
                    )

    return {"status": "ok"}


# ── Reliability API endpoints ─────────────────────────────────────────────────

@router.get("/reliability/stats")
def reliability_stats(
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    return get_reliability_stats(db)


@router.get("/reliability/manual-review")
def manual_review_queue(
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    from orderr_core.models.inbound_message import InboundMessage
    msgs = get_all_failed_messages(db)
    return {
        "count": len(msgs),
        "messages": [
            {
                "id":                m.id,
                "customer_phone":    m.customer_phone,
                "raw_message":       m.raw_message,
                "received_at":       m.received_at.isoformat() if m.received_at else None,
                "processing_status": m.processing_status,
                "failure_reason":    m.failure_reason or "Unknown",
                "attempts":          m.processing_attempts,
                "ack_failed":        m.ack_failed,
                "linked_order_id":   m.linked_order_id,
            }
            for m in msgs
        ],
    }


@router.post("/reliability/manual-review/{msg_id}/retry")
def manual_retry(
    msg_id: int,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    from orderr_core.models.inbound_message import InboundMessage

    msg = db.query(InboundMessage).filter(InboundMessage.id == msg_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if not msg.raw_message:
        raise HTTPException(status_code=400, detail="No raw message to retry")

    msg.processing_status   = "RECEIVED"
    msg.failure_reason      = None
    msg.processing_attempts = 0
    db.commit()

    try:
        result = process_incoming_order(
            db             = db,
            customer_phone = msg.customer_phone,
            message        = msg.raw_message,
        )
        if result.get("order_id"):
            msg.linked_order_id = result["order_id"]
        msg.processing_status = "CONFIRMED"
        db.commit()
        return {"status": "success", "order_id": result.get("order_id")}
    except Exception as e:
        record_failure(db, msg, reason=f"Manual retry failed: {e}")
        raise HTTPException(status_code=500, detail=f"Retry failed: {e}")


@router.post("/reliability/manual-review/{msg_id}/resolve")
def mark_resolved(
    msg_id: int,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    from orderr_core.models.inbound_message import InboundMessage

    msg = db.query(InboundMessage).filter(InboundMessage.id == msg_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    msg.processing_status = "CANCELLED"
    db.commit()
    return {"status": "resolved"}
