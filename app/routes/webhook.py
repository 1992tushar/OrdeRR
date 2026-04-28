from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.order_service import process_incoming_order
import json

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