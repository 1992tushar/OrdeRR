import json
import logging
import os
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.inbound_message import InboundMessage
from app.services.notifier import send_whatsapp_message

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

RETRY_SCHEDULE_MINUTES = [1, 5, 15, 30, 60]

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME    = os.getenv("PLANT_NAME", "OrdeRR")

ACK_MESSAGE = "✅ Order received. Processing now."

# FIX: webhook.py transitions RECEIVED → PARSING directly (no ACK step),
# then PARSING → CONFIRMED on success. Both paths must be valid.
VALID_TRANSITIONS = {
    "RECEIVED":      {"PROCESSING", "ACK_SENT", "PARSING", "FAILED", "CONFIRMED"},
    "PROCESSING":    {"CONFIRMED", "FAILED", "MANUAL_REVIEW"},
    "ACK_SENT":      {"PARSING", "FAILED"},
    "PARSING":       {"PARSED", "NOTE", "FAILED", "CONFIRMED"},
    "PARSED":        {"ORDER_CREATED", "FAILED"},
    "ORDER_CREATED": {"CONFIRMED", "FAILED"},
    "CONFIRMED":     {"CONFIRMED"},
    "NOTE":          set(),
    "FAILED":        {"PARSING", "PROCESSING", "ACK_SENT", "MANUAL_REVIEW"},
    "MANUAL_REVIEW": {"ORDER_CREATED", "CANCELLED"},
    "CANCELLED":     set(),
}


def transition(msg: InboundMessage, new_status: str, db: Session, failure_reason: str = None):

    allowed = VALID_TRANSITIONS.get(msg.processing_status, set())
    if new_status not in allowed:
        raise ValueError(
            f"Invalid transition {msg.processing_status} → {new_status} for msg id={msg.id}"
        )

    allowed = VALID_TRANSITIONS.get(msg.processing_status, set())
    msg.processing_status = new_status
    if failure_reason:
        msg.failure_reason = failure_reason
    msg.updated_at = datetime.now(IST)
    try:
        db.commit()
    except SQLAlchemyError as e:
        logger.critical("Could not commit state transition for msg id=%s: %s", msg.id, e)
        db.rollback()


def persist_raw_message(
    db: Session,
    *,
    meta_message_id: str | None,
    customer_phone: str,
    raw_message: str | None,
    payload_json: str | None,
    message_type: str = "text",
) -> InboundMessage | None:
    # Idempotency check
    if meta_message_id:
        existing = db.query(InboundMessage).filter(InboundMessage.meta_message_id == meta_message_id).first()
        if existing:
            logger.info("Duplicate webhook: meta_id=%s", meta_message_id)
            existing.is_duplicate = True
            try:
                db.commit()
            except SQLAlchemyError:
                db.rollback()
            return existing

    msg = InboundMessage(
        meta_message_id  = meta_message_id,
        customer_phone   = customer_phone,
        raw_message      = raw_message,
        payload_json     = payload_json,
        message_type     = message_type,
        processing_status = "RECEIVED",
        received_at      = datetime.now(IST),
    )

    for attempt in range(3):
        try:
            db.add(msg)
            db.commit()
            db.refresh(msg)
            logger.info("Persisted inbound msg id=%s phone=%s", msg.id, customer_phone)
            return msg
        except SQLAlchemyError as e:
            db.rollback()
            logger.critical("Persistence failure attempt %d/3 for phone=%s: %s", attempt + 1, customer_phone, e)

    _alert_persistence_failure(customer_phone, raw_message, meta_message_id)
    return None


def send_acknowledgement(db: Session, msg: InboundMessage) -> bool:
    for attempt in range(1, 4):
        try:
            send_whatsapp_message(msg.customer_phone, ACK_MESSAGE)
            msg.acknowledged_to_customer = True
            msg.ack_attempts             = attempt
            msg.ack_failed               = False
            try:
                db.commit()
            except SQLAlchemyError:
                db.rollback()
            transition(msg, "ACK_SENT", db)
            logger.info("Ack sent to %s msg id=%s", msg.customer_phone, msg.id)
            return True
        except Exception as e:
            logger.warning("Ack attempt %d/3 failed for msg id=%s: %s", attempt, msg.id, e)

    msg.ack_failed   = True
    msg.ack_attempts = 3
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
    _alert_ack_failure(msg)
    return False


def record_failure(db: Session, msg: InboundMessage, reason: str):
    msg.processing_attempts += 1
    transition(msg, "FAILED", db, failure_reason=reason)
    _alert_processing_failure(msg, reason)
    logger.error("Message id=%s FAILED: %s", msg.id, reason)


def record_manual_review(db: Session, msg: InboundMessage, reason: str):
    msg.processing_attempts += 1
    transition(msg, "MANUAL_REVIEW", db, failure_reason=reason)
    _alert_manual_review(msg, reason)
    logger.error("Message id=%s → MANUAL_REVIEW: %s", msg.id, reason)


def should_retry(msg: InboundMessage) -> bool:
    return msg.processing_status == "FAILED" and msg.processing_attempts <= len(RETRY_SCHEDULE_MINUTES)


def mark_retry_attempt(db: Session, msg: InboundMessage):
    msg.processing_attempts += 1
    msg.last_retry_at = datetime.now(IST)
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()


def get_messages_pending_retry(db: Session) -> list:
    from datetime import timedelta
    now = datetime.now(IST)
    failed_msgs = (
        db.query(InboundMessage)
        .filter(
            InboundMessage.processing_status == "FAILED",
            InboundMessage.processing_attempts <= len(RETRY_SCHEDULE_MINUTES),
        )
        .all()
    )

    due = []
    for msg in failed_msgs:
        attempt = msg.processing_attempts
        if attempt >= len(RETRY_SCHEDULE_MINUTES):
            record_manual_review(db, msg, f"Retry schedule exhausted after {attempt} attempts")
            continue
        delay_minutes = RETRY_SCHEDULE_MINUTES[max(attempt - 1, 0)]
        if msg.last_retry_at:
            lr = msg.last_retry_at
            if lr.tzinfo is None:
                lr = lr.replace(tzinfo=IST)
            if now >= lr + timedelta(minutes=delay_minutes):
                due.append(msg)
        else:
            due.append(msg)
    return due


def get_messages_for_manual_review(db: Session) -> list:
    return (
        db.query(InboundMessage)
        .filter(InboundMessage.processing_status == "MANUAL_REVIEW")
        .order_by(InboundMessage.received_at.asc())
        .all()
    )


def get_all_failed_messages(db: Session, limit: int = 100) -> list:
    """Returns both FAILED and MANUAL_REVIEW messages for the dashboard."""
    return (
        db.query(InboundMessage)
        .filter(InboundMessage.processing_status.in_(["FAILED", "MANUAL_REVIEW"]))
        .order_by(InboundMessage.received_at.desc())
        .limit(limit)
        .all()
    )


def get_reliability_stats(db: Session) -> dict:
    from sqlalchemy import func
    today = datetime.now(IST).date()

    total = db.query(InboundMessage).filter(func.date(InboundMessage.received_at) == today).count()
    confirmed = db.query(InboundMessage).filter(
        func.date(InboundMessage.received_at) == today,
        InboundMessage.processing_status == "CONFIRMED",
    ).count()
    failed_today = db.query(InboundMessage).filter(
        func.date(InboundMessage.received_at) == today,
        InboundMessage.processing_status.in_(["FAILED", "MANUAL_REVIEW"]),
    ).count()
    manual_review_total = db.query(InboundMessage).filter(
        InboundMessage.processing_status == "MANUAL_REVIEW"
    ).count()
    ack_failures = db.query(InboundMessage).filter(
        func.date(InboundMessage.received_at) == today,
        InboundMessage.ack_failed == True,
    ).count()
    duplicates = db.query(InboundMessage).filter(
        func.date(InboundMessage.received_at) == today,
        InboundMessage.is_duplicate == True,
    ).count()

    return {
        "total_today":         total,
        "confirmed_today":     confirmed,
        "failed_today":        failed_today,
        "manual_review_total": manual_review_total,
        "ack_failures":        ack_failures,
        "duplicate_webhooks":  duplicates,
        "has_issues":          (failed_today + manual_review_total) > 0,
    }


# ── Internal alerts ───────────────────────────────────────────────────────────

def _alert_persistence_failure(customer_phone, raw_message, meta_id):
    msg = (
        f"🚨 *CRITICAL — Persistence Failure*\n\n"
        f"Could not save inbound message after 3 attempts.\n\n"
        f"📱 Phone: {customer_phone}\n"
        f"🆔 Meta ID: {meta_id}\n"
        f"📝 Message: {(raw_message or '')[:200]}\n\n"
        f"⚠️ Manual intervention required immediately."
    )
    try:
        if MANAGER_PHONE:
            send_whatsapp_message(MANAGER_PHONE, msg)
    except Exception:
        pass
    logger.critical("PERSISTENCE FAILURE for phone=%s", customer_phone)


def _alert_ack_failure(msg: InboundMessage):
    alert = (
        f"⚠️ *Ack Failure — {PLANT_NAME}*\n\n"
        f"Could not send 'order received' to customer.\n\n"
        f"📱 Phone: {msg.customer_phone}\n"
        f"📝 Message: {(msg.raw_message or '')[:200]}\n\n"
        f"Customer may not know their message arrived."
    )
    try:
        if MANAGER_PHONE:
            send_whatsapp_message(MANAGER_PHONE, alert)
    except Exception:
        pass


def _alert_processing_failure(msg: InboundMessage, reason: str):
    alert = (
        f"❌ *Processing Failure — {PLANT_NAME}*\n\n"
        f"Could not process order from customer.\n\n"
        f"📱 Phone: {msg.customer_phone}\n"
        f"📝 Message: {(msg.raw_message or '')[:300]}\n"
        f"🕐 Received: {msg.received_at.strftime('%d %b %Y %I:%M %p') if msg.received_at else 'unknown'}\n"
        f"❗ Reason: {reason}\n"
        f"🔄 Attempt: {msg.processing_attempts}"
    )
    try:
        if MANAGER_PHONE:
            send_whatsapp_message(MANAGER_PHONE, alert)
    except Exception:
        pass


def _alert_manual_review(msg: InboundMessage, reason: str):
    alert = (
        f"🔴 *MANUAL REVIEW REQUIRED — {PLANT_NAME}*\n\n"
        f"Retries exhausted. Please process manually.\n\n"
        f"📱 Phone: {msg.customer_phone}\n"
        f"📝 Message: {(msg.raw_message or '')[:300]}\n"
        f"🕐 Received: {msg.received_at.strftime('%d %b %Y %I:%M %p') if msg.received_at else 'unknown'}\n"
        f"❗ Last failure: {reason}\n\n"
        f"⚠️ Open dashboard → Failed tab to retry or resolve."
    )
    try:
        if MANAGER_PHONE:
            send_whatsapp_message(MANAGER_PHONE, alert)
    except Exception:
        pass


# Alias for backwards compatibility with tests
record_inbound = persist_raw_message

def record_inbound(db: Session, customer_phone: str, raw_message: str, meta_message_id: str, payload: dict) -> InboundMessage | None:
    return persist_raw_message(
        db,
        meta_message_id=meta_message_id,
        customer_phone=customer_phone,
        raw_message=raw_message,
        payload_json=json.dumps(payload) if payload else None,
    )