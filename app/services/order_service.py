from sqlalchemy.orm import Session
from sqlalchemy import cast, Date, func

from app.models.order import Order
from app.services.parser import parse_order
from app.services.notifier import (
    send_order_confirmation,
    send_manager_alert,
    send_unclear_order_alert
)
from app.services.template_parser import parse_template_order
from app.services.customer_service import (
    get_customer_by_phone,
    create_new_customer
)

import json
import os

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")


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
    3. Send confirmation to customer
    4. Alert plant manager
    """

    from app.services.notifier import send_whatsapp_message

    # Lookup customer
    customer = get_customer_by_phone(db, customer_phone)

    # First-time customer
    if not customer:
        customer = create_new_customer(db, customer_phone)

        send_whatsapp_message(
            customer_phone,
            "👋 Welcome to BBC Ordering!\n\n"
            "Please reply with your restaurant name to continue."
        )

        # Return consistent shape — order_id is None for new customers
        return {
            "order_id": None,
            "status": "awaiting_restaurant_name",
            "parsed": None
        }

    # Customer onboarding pending
    if customer.onboarding_status == "awaiting_name":

        customer.restaurant_name = message.strip()
        customer.onboarding_status = "active"
        db.commit()

        send_whatsapp_message(
            customer_phone,
            f"✅ Welcome {customer.restaurant_name}!\n\n"
            f"You can now place orders.\n\n"
            f"Type 'order' to see menu."
        )

        return {
            "order_id": None,
            "status": "customer_onboarded",
            "parsed": None
        }

    # Active customer — process order
    restaurant_name = customer.restaurant_name

    # Detect template-style structured order
    template_keywords = [
        "whole broiler",
        "breast boneless",
        "leg boneless",
        "wings",
        "drumsticks"
    ]

    is_template_order = any(
        keyword in message.lower()
        for keyword in template_keywords
    )

    if is_template_order:
        parsed = parse_template_order(customer_phone, message)
        # Fallback to AI parser if template parse failed
        if parsed.get("is_unclear"):
            parsed = parse_order(customer_phone, message)
    else:
        parsed = parse_order(customer_phone, message)

    # Save order
    order = Order(
        plant_name=os.getenv("PLANT_NAME", "Fluffy"),
        customer_name=restaurant_name,
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

    # Send alerts
    if parsed.get("is_unclear"):
        send_unclear_order_alert(
            manager_phone=MANAGER_PHONE,
            customer_phone=customer_phone,
            raw_message=message,
            unclear_reason=parsed.get("unclear_reason", "Unknown reason")
        )
    else:
        send_order_confirmation(
            customer_phone=customer_phone,
            parsed=parsed
        )
        send_manager_alert(
            manager_phone=MANAGER_PHONE,
            customer_phone=customer_phone,
            parsed=parsed,
            restaurant_name=restaurant_name
        )

    order.confirmation_sent = True
    order.forwarded_to_manager = True
    db.commit()

    return {
        "order_id": order.id,
        "customer_phone": customer_phone,
        "parsed": parsed,
        "status": order.status,
        "saved": True
    }


def get_all_orders(db: Session) -> list:
    return db.query(Order).order_by(
        Order.created_at.desc()
    ).all()


def get_unclear_orders(db: Session) -> list:
    # Use .is_(True) instead of == True
    return db.query(Order).filter(
        Order.is_unclear.is_(True)
    ).order_by(
        Order.created_at.desc()
    ).all()


def get_todays_orders(db: Session) -> list:
    return db.query(Order).filter(
        cast(Order.created_at, Date) == func.current_date()
    ).order_by(
        Order.created_at.asc()
    ).all()