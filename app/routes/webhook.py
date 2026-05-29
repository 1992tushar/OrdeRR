import json
import logging
import os
import hmac
import hashlib

from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.order_service import process_incoming_order
from app.services.reporter import send_daily_report
from app.services.product_catalog import generate_menu_template
from app.services.notifier import send_whatsapp_message
from app.services.message_journal import (
    persist_raw_message,
    send_acknowledgement,
    record_failure,
    record_manual_review,
    transition,
    get_reliability_stats,
    get_all_failed_messages,
    get_messages_for_manual_review,
)
from app.auth import require_auth

logger = logging.getLogger(__name__)
router = APIRouter()

MENU_TRIGGER = {"menu", "order", "show menu", "send menu", "place order"}


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
    username: str = Depends(require_auth),
):
    try:
        payload        = await request.json()
        customer_phone = payload.get("phone", "919999999999")
        message_text   = payload.get("message", "")
        if not message_text:
            return {"status": "error", "message": "No message provided"}
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
    if mode == "subscribe" and token == os.getenv("META_VERIFY_TOKEN", ""):
        return int(challenge)
    return {"error": "Invalid token"}


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
                message_type    = message.get("type", "")
                meta_message_id = message.get("id")

                if message_type == "text":
                    raw_message = message.get("text", {}).get("body", "")
                elif message_type == "image":
                    raw_message = message.get("image", {}).get("caption", "Photo order received")
                else:
                    raw_message = f"[{message_type} message]"

                if not customer_phone:
                    continue

                # ── REQ 1.1: PERSIST RAW MESSAGE FIRST ───────────────────────
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

                # ── REQ 1.3: IDEMPOTENCY ──────────────────────────────────────
                if inbound_msg.is_duplicate:
                    logger.info("Duplicate webhook meta_id=%s — safe skip", meta_message_id)
                    continue

                # Unsupported type — persisted for audit, no processing
                if message_type not in ("text", "image"):
                    logger.info("Unsupported type %s from %s — persisted only", message_type, customer_phone)
                    continue

                if not raw_message:
                    continue

                msg_lower       = raw_message.strip().lower()
                is_menu_trigger = msg_lower in MENU_TRIGGER

                # ── REQ 2.1: IMMEDIATE ACK BEFORE PARSING ────────────────────
                if not is_menu_trigger:
                    send_acknowledgement(db, inbound_msg)

                # ── MENU TRIGGER ──────────────────────────────────────────────
                if is_menu_trigger:
                    try:
                        send_whatsapp_message(customer_phone, generate_menu_template())
                        transition(inbound_msg, "CONFIRMED", db)
                    except Exception as e:
                        record_failure(db, inbound_msg, f"Menu send failed: {e}")
                    continue

                # ── PARSING ───────────────────────────────────────────────────
                transition(inbound_msg, "PARSING", db)

                try:
                    result = process_incoming_order(
                        db             = db,
                        customer_phone = customer_phone,
                        message        = raw_message,
                        is_photo       = (message_type == "image"),
                    )

                    if result.get("order_id"):
                        inbound_msg.linked_order_id = result["order_id"]

                    inbound_msg.processing_status = "CONFIRMED"
                    db.commit()

                    logger.info("msg id=%s → order_id=%s", inbound_msg.id, result.get("order_id"))

                except Exception as e:
                    logger.exception("Processing failed for msg id=%s phone=%s", inbound_msg.id, customer_phone)
                    record_failure(db, inbound_msg, reason=f"{type(e).__name__}: {str(e)[:300]}")

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
    from app.models.inbound_message import InboundMessage

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
    from app.models.inbound_message import InboundMessage

    msg = db.query(InboundMessage).filter(InboundMessage.id == msg_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    msg.processing_status = "CANCELLED"
    db.commit()
    return {"status": "resolved"}
