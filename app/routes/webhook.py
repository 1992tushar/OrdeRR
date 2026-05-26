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

MENU_TRIGGER = {"menu", "order", "show menu", "send menu", "place order"}


def verify_meta_signature(body: bytes, signature_header: str) -> bool:
    app_secret = os.getenv("META_APP_SECRET", "")
    if not app_secret:
        print("⚠️  META_APP_SECRET not set — skipping signature verification")
        return True
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
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
        payload      = await request.json()
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
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value    = change.get("value", {})
                messages = value.get("messages", [])

                for message in messages:
                    customer_phone = message.get("from", "")
                    message_type   = message.get("type", "")
                    is_photo       = False

                    if message_type == "text":
                        message_text = message.get("text", {}).get("body", "")
                    elif message_type == "image":
                        message_text = message.get("image", {}).get("caption", "Photo order received")
                        is_photo = True
                    else:
                        continue

                    if not message_text or not customer_phone:
                        continue

                    # Menu on demand handled here too (for non-onboarded customers
                    # who somehow type "menu" — order_service handles it after onboarding)
                    if message_text.strip().lower() in MENU_TRIGGER:
                        send_whatsapp_message(customer_phone, generate_menu_template())
                        continue

                    process_incoming_order(
                        db=db,
                        customer_phone=customer_phone,
                        message=message_text,
                        is_photo=is_photo,
                    )

        return {"status": "ok"}

    except Exception as e:
        return {"status": "error", "message": str(e)}
