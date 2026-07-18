import logging
import os
import requests
from datetime import datetime, timezone, timedelta

from orderr_core.services.notifier import send_whatsapp_message

logger = logging.getLogger(__name__)

from orderr_core.constants import IST
from orderr_core.config import MANAGER_PHONE, PLANT_NAME
META_ACCESS_TOKEN    = os.getenv("META_ACCESS_TOKEN", "")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")

META_API_URL = f"https://graph.facebook.com/v21.0/{META_PHONE_NUMBER_ID}"


def check_webhook_health():
    """
    Called by scheduler every 30 minutes.
    Pings Meta Graph API to verify the access token and phone number are valid.
    Alerts manager only if the API call actually fails — meaning something is
    genuinely broken (token expired, number suspended, Meta-side issue).
    Silence (no inbound messages) is NOT treated as a failure.
    """
    if not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
        logger.warning("Webhook health: META_ACCESS_TOKEN or META_PHONE_NUMBER_ID not set — skipping check")
        return

    now_ist = datetime.now(IST)

    try:
        response = requests.get(
            META_API_URL,
            params={"access_token": META_ACCESS_TOKEN},
            timeout=10,
        )

        if response.status_code == 200:
            logger.info("Webhook health: Meta API ping OK ✓ (%s)", now_ist.strftime("%H:%M IST"))
            return

        # Non-200 — something is wrong
        error_data = response.json() if response.content else {}
        error_msg  = error_data.get("error", {}).get("message", f"HTTP {response.status_code}")
        error_code = error_data.get("error", {}).get("code", "unknown")

        logger.error(
            "Webhook health: Meta API ping FAILED — code=%s msg=%s",
            error_code, error_msg
        )
        _send_failure_alert(now_ist, error_code, error_msg)

    except requests.Timeout:
        logger.error("Webhook health: Meta API ping timed out")
        _send_failure_alert(now_ist, "TIMEOUT", "Meta API did not respond within 10 seconds")

    except requests.ConnectionError as e:
        logger.error("Webhook health: Meta API connection error — %s", e)
        _send_failure_alert(now_ist, "CONNECTION_ERROR", "Could not reach graph.facebook.com")

    except Exception as e:
        logger.error("Webhook health check failed unexpectedly: %s", e)


def _send_failure_alert(now_ist: datetime, error_code, error_msg: str):
    alert = (
        f"🚨 *Webhook Health Alert — {PLANT_NAME}*\n\n"
        f"Meta API ping failed — something is genuinely broken.\n\n"
        f"🕐 Time: {now_ist.strftime('%d %b %Y %I:%M %p IST')}\n"
        f"❌ Error: {error_msg} (code: {error_code})\n\n"
        f"Possible causes:\n"
        f"• Access token expired or revoked\n"
        f"• WhatsApp number suspended\n"
        f"• Meta API outage\n\n"
        f"Check Meta developer console immediately."
    )
    try:
        if MANAGER_PHONE:
            send_whatsapp_message(MANAGER_PHONE, alert)
    except Exception as e:
        logger.error("Could not send webhook health alert: %s", e)
