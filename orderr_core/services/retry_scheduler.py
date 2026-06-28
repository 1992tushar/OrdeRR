import logging
from orderr_core.database import SessionLocal
from orderr_core.services.message_journal import (
    get_messages_pending_retry,
    mark_retry_attempt,
    record_failure,
    record_manual_review,
    transition,
    RETRY_SCHEDULE_MINUTES,
)
from orderr_core.services.order_service import process_incoming_order

logger = logging.getLogger(__name__)


def retry_failed_messages():
    """
    Called by scheduler every 1 minute.
    Retries FAILED messages: 1 → 5 → 15 → 30 → 60 min → MANUAL_REVIEW.
    """
    db = SessionLocal()
    try:
        due_messages = get_messages_pending_retry(db)
        if not due_messages:
            return

        logger.info("Retry job: %d messages due", len(due_messages))

        for msg in due_messages:
            if not msg.raw_message:
                record_manual_review(db, msg, "No raw message available for retry")
                continue

            logger.info("Retrying msg id=%s phone=%s attempt=%d", msg.id, msg.customer_phone, msg.processing_attempts + 1)
            mark_retry_attempt(db, msg)

            try:
                msg.processing_status = "PARSING"
                db.commit()

                result = process_incoming_order(
                    db             = db,
                    customer_phone = msg.customer_phone,
                    message        = msg.raw_message,
                )

                if result.get("order_id"):
                    msg.linked_order_id = result["order_id"]

                msg.processing_status = "CONFIRMED"
                db.commit()
                logger.info("Retry success: msg id=%s → order_id=%s", msg.id, result.get("order_id"))

            except Exception as e:
                logger.warning("Retry failed for msg id=%s: %s", msg.id, e)
                if msg.processing_attempts >= len(RETRY_SCHEDULE_MINUTES):
                    record_manual_review(db, msg, f"Retry schedule exhausted. Last: {type(e).__name__}: {str(e)[:200]}")
                else:
                    record_failure(db, msg, f"Retry {msg.processing_attempts}/{len(RETRY_SCHEDULE_MINUTES)}: {str(e)[:200]}")

    except Exception as e:
        logger.critical("Retry scheduler crashed: %s", e)
    finally:
        db.close()

