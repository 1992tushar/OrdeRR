from sqlalchemy.orm import Session
from app.models.order import Order
from app.services.parser import parse_order
import json
import os
from dotenv import load_dotenv

load_dotenv()

def process_incoming_order(
    db: Session,
    customer_phone: str,
    message: str,
    is_photo: bool = False
) -> dict:
    """
    Full pipeline:
    1. Parse incoming WhatsApp message
    2. Save to database
    3. Return structured result
    """

    # Step 1 — Parse order using AI
    parsed = parse_order(customer_phone, message)

    # Step 2 — Save to database
    order = Order(
        plant_name=os.getenv("PLANT_NAME", "Fluffy"),
        customer_phone=customer_phone,
        raw_message=message,
        is_photo_order=is_photo,
        parsed_items=json.dumps(parsed.get("items", [])),
        delivery_date=parsed.get("delivery_date"),
        delivery_time=parsed.get("delivery_time"),
        is_unclear=parsed.get("is_unclear", False),
        unclear_reason=parsed.get("unclear_reason"),
        status="received"
    )

    db.add(order)
    db.commit()
    db.refresh(order)

    # Step 3 — Return result
    return {
        "order_id": order.id,
        "customer_phone": customer_phone,
        "parsed": parsed,
        "status": order.status,
        "saved": True
    }

def get_all_orders(db: Session) -> list:
    """Get all orders sorted by latest first"""
    return db.query(Order).order_by(Order.created_at.desc()).all()

def get_unclear_orders(db: Session) -> list:
    """Get all unclear orders that need manual review"""
    return db.query(Order).filter(
        Order.is_unclear == True
    ).order_by(Order.created_at.desc()).all()

def get_todays_orders(db: Session) -> list:
    """Get all orders received today"""
    from sqlalchemy import func, cast, Date
    today = func.date(Order.created_at)
    return db.query(Order).filter(
        cast(Order.created_at, Date) == func.current_date()
    ).order_by(Order.created_at.asc()).all()