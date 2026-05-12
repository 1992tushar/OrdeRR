from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.order_service import process_incoming_order
from app.services.reporter import send_morning_report, send_evening_report
import json
from app.services.product_catalog import generate_menu_template
from app.services.notifier import send_whatsapp_message

router = APIRouter()

@router.get("/health")
def health_check():
    return {"status": "OrdeRR webhook is running"}


@router.post("/interakt")
async def interakt_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives incoming WhatsApp messages from Interakt.
    Parses order and saves to database automatically.
    """
    try:
        # Get raw payload from Interakt
        payload = await request.json()

        # Extract message details from Interakt payload format
        # Interakt sends messages in this structure
        message_data = payload.get("data", {})
        customer = message_data.get("customer", {})
        message = message_data.get("message", {})

        # Extract customer phone
        customer_phone = customer.get("phone_number", "")
        customer_name = customer.get("name", "")

        # Extract message content
        message_type = message.get("type", "")
        
        if message_type == "text":
            message_text = message.get("text", {}).get("body", "")
            is_photo = False
        elif message_type == "image":
            # Photo order — use caption if available
            message_text = message.get("image", {}).get("caption", "Photo order received")
            is_photo = True
        else:
            # Unsupported message type
            return {"status": "ignored", "reason": f"Unsupported message type: {message_type}"}

        # Skip if empty message
        if not message_text or not customer_phone:
            return {"status": "ignored", "reason": "Empty message or phone"}



        # Process the order
        message_text_clean = message_text.strip().lower()

if message_text_clean == "order":

    menu_template = generate_menu_template()

    send_whatsapp_message(
        customer_phone,
        menu_template
    )

    return {
        "status": "menu_template_sent"
    }
        result = process_incoming_order(
            db=db,
            customer_phone=customer_phone,
            message=message_text,
            is_photo=is_photo
        )

        return {
            "status": "success",
            "order_id": result["order_id"],
            "customer": customer_phone,
            "items_parsed": len(result["parsed"].get("items", [])),
            "is_unclear": result["parsed"].get("is_unclear", False)
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/test")
async def test_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Test endpoint — simulates receiving a WhatsApp order.
    Use this with Postman for testing without Interakt.
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

        return {
            "status": "success",
            "order_id": result["order_id"],
            "customer": customer_phone,
            "parsed": result["parsed"],
            "is_unclear": result["parsed"].get("is_unclear", False)
        }

    except Exception as e:
        return {
            "status": "error", 
            "message": str(e)
        }


@router.post("/report/morning")
def trigger_morning_report(db: Session = Depends(get_db)):
    """Manually trigger morning report — for testing"""
    send_morning_report(db)
    return {"status": "Morning report sent"}


@router.post("/report/evening")
def trigger_evening_report(db: Session = Depends(get_db)):
    """Manually trigger evening report — for testing"""
    send_evening_report(db)
    return {"status": "Evening report sent"}

@router.get("/meta")
async def meta_webhook_verify(request: Request):
    """Meta webhook verification"""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    if mode == "subscribe" and token == "orderr_fluffy_2026":
        return int(challenge)
    return {"error": "Invalid token"}

@router.post("/meta")
async def meta_webhook(request: Request, db: Session = Depends(get_db)):
    """Receives incoming WhatsApp messages from Meta Cloud API"""
    try:
        payload = await request.json()
        
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                
                for message in messages:
                    customer_phone = message.get("from", "")
                    message_type = message.get("type", "")
                    
                    if message_type == "text":
                        message_text = message.get("text", {}).get("body", "")
                    elif message_type == "image":
                        message_text = message.get("image", {}).get("caption", "Photo order received")
                    else:
                        continue
                    
                    if message_text and customer_phone:
                        process_incoming_order(
                            db=db,
                            customer_phone=customer_phone,
                            message=message_text
                        )
        
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}        