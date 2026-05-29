import logging
import os
from datetime import datetime, timezone, timedelta

from app.database import SessionLocal
from app.models.inbound_message import InboundMessage
from app.services.notifier import send_whatsapp_message

logger = logging.getLogger(__name__)

IST           = timezone(timedelta(hours=5, minutes=30))
MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
PLANT_NAME    = os.getenv("PLANT_NAME", "OrdeRR")

BUSINESS_HOUR_START     = 8
BUSINESS_HOUR_END       = 21
NO_MESSAGE_ALERT_MINUTES = 120
_ALERT_COOLDOWN_HOURS   = 2

_last_no_message_alert = None


def check_webhook_health():
    """
    Called by scheduler every 30 minutes.
    Alerts manager if no messages received in the last 2 hours during business hours.
    """
    global _last_no_message_alert

    now_ist = datetime.now(IST)
    if not (BUSINESS_HOUR_START <= now_ist.hour < BUSINESS_HOUR_END):
        return

    db = SessionLocal()
    try:
        cutoff = now_ist - timedelta(minutes=NO_MESSAGE_ALERT_MINUTES)
        recent_count = (
            db.query(InboundMessage)
            .filter(
                InboundMessage.received_at >= cutoff,
                InboundMessage.is_duplicate == False,
            )
            .count()
        )

        if recent_count == 0:
            cooldown_ok = (
                _last_no_message_alert is None
                or (now_ist - _last_no_message_alert).total_seconds() > _ALERT_COOLDOWN_HOURS * 3600
            )
            if cooldown_ok:
                _send_no_message_alert(now_ist)
                _last_no_message_alert = now_ist
        else:
            logger.info("Webhook health: %d messages in last %d min — OK", recent_count, NO_MESSAGE_ALERT_MINUTES)
            _last_no_message_alert = None

    except Exception as e:
        logger.error("Webhook health check failed: %s", e)
    finally:
        db.close()


def _send_no_message_alert(now_ist):
    alert = (
        f"⚠️ *Webhook Health Alert — {PLANT_NAME}*\n\n"
        f"No inbound WhatsApp messages in the last {NO_MESSAGE_ALERT_MINUTES} minutes.\n\n"
        f"🕐 Time: {now_ist.strftime('%d %b %Y %I:%M %p IST')}\n\n"
        f"Possible causes:\n"
        f"• Meta webhook issue\n"
        f"• Render service downtime\n"
        f"• WhatsApp number issue\n\n"
        f"Check Meta developer console and Render logs."
    )
    try:
        if MANAGER_PHONE:
            send_whatsapp_message(MANAGER_PHONE, alert)
    except Exception as e:
        logger.error("Could not send webhook health alert: %s", e)
    logger.warning("WEBHOOK HEALTH: No messages in last %d minutes", NO_MESSAGE_ALERT_MINUTES)
