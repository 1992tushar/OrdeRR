from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session
import hmac
import hashlib
import json
import os

from app.database import get_db
from app.services.order_service import process_incoming_order
from app.services.reporter import send_daily_report
from app.services.product_catalog import generate_menu_template
from app.services.notifier import send_whatsapp_message
from app.auth import require_auth

router = APIRouter()


def verify_meta_signature(body: bytes, signature_header: str) -> bool:
    """
    Verify that the request genuinely came from Meta
    using HMAC-SHA256 signature check.
    """

    app_secret = os.getenv("META_APP_SECRET", "")

    if not app_secret:
        # If secret not configured, skip in dev mode
        print("⚠️  META_APP_SECRET not set — skipping signature verification")
        return True

    if not signature_header.startswith("sha256="):
        return False

    expected_signature = hmac.new(
        app_secret.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()

    received_signature = signature_header[len("sha256="):]

    # Use compare_digest to prevent timing attacks
    return hmac.compare_digest(expected_signature, received_signature)


@router.get("/health")
def health_check():
    return {"status": "OrdeRR webhook is running"}


@router.post("/test")
async def test_webhook(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """
    Test endpoint for Postman/manual testing.
    Requires Basic Auth.
    """

    try:
        payload = await request.json()

        customer_phone = payload.get("phone", "919999999999")
        message_text = payload.get("message", "")

        if not message_text:
            return {"status": "error", "message": "No message provided"}

        result = process_incoming_order(
            db=db,
            customer_phone=customer_phone,
            message=message_text
        )

        # Use .get() — result shape varies for new vs existing customers
        return {
            "status": "success",
            "order_id": result.get("order_id"),
            "customer": customer_phone,
            "parsed": result.get("parsed"),
            "is_unclear": result.get("parsed", {}).get("is_unclear", False)
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/report")
def trigger_report(
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """Manually trigger the daily report. Requires Basic Auth."""
    send_daily_report(db)
    return {"status": "Daily report sent"}


@router.get("/meta")
async def meta_webhook_verify(request: Request):
    """
    Meta webhook verification — public, Meta calls this.
    Token read from env instead of hardcoded.
    """

    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    verify_token = os.getenv("META_VERIFY_TOKEN", "")

    if mode == "subscribe" and token == verify_token:
        return int(challenge)

    return {"error": "Invalid token"}


@router.post("/meta")
async def meta_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Receives incoming WhatsApp messages from Meta Cloud API.
    Public — Meta calls this. Secured via signature verification.
    """

    # Read raw body BEFORE json.loads() — body stream can only be read once
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_meta_signature(body, signature):
        raise HTTPException(
            status_code=403,
            detail="Invalid webhook signature"
        )

    try:
        payload = json.loads(body)

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])

                for message in messages:
                    customer_phone = message.get("from", "")
                    message_type = message.get("type", "")
                    is_photo = False

                    if message_type == "text":
                        message_text = message.get("text", {}).get("body", "")

                    elif message_type == "image":
                        message_text = message.get("image", {}).get(
                            "caption", "Photo order received"
                        )
                        is_photo = True

                    else:
                        continue

                    if not message_text or not customer_phone:
                        continue

                    message_text_clean = message_text.strip().lower()

                    if message_text_clean == "order":
                        menu_template = generate_menu_template()
                        send_whatsapp_message(customer_phone, menu_template)
                        continue

                    process_incoming_order(
                        db=db,
                        customer_phone=customer_phone,
                        message=message_text,
                        is_photo=is_photo
                    )

        return {"status": "ok"}

    except Exception as e:
        return {"status": "error", "message": str(e)}