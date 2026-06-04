"""
admin.py — Admin API routes (Basic Auth protected)

Salesperson:  GET/POST /admin/salespersons
              PUT/DELETE /admin/salespersons/{id}
Customer:     GET /admin/customers
              GET /admin/customers/unassigned
              GET /admin/customers/{id}/orders
              POST /admin/customers/{id}/assign
              PUT  /admin/customers/{id}/status
Pending:      GET /admin/pending
Window:       GET /admin/window-status
"""

import os
import json
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.auth import require_auth
from app.database import get_db
from app.models.customer import Customer
from app.models.order import Order
from app.models.salesperson import Salesperson
from app.models.inbound_message import InboundMessage
from app.services.customer_service import normalize_phone
from app.services.notifier import send_whatsapp_message
from app.services.pending_orders import get_pending_customers, get_delivery_date_for_now

router     = APIRouter()
PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")
MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
IST        = timezone(timedelta(hours=5, minutes=30))


# ── Schemas ───────────────────────────────────────────────────────────────────

class SalespersonCreate(BaseModel):
    name: str
    phone: str

class SalespersonUpdate(BaseModel):
    name:   Optional[str]  = None
    phone:  Optional[str]  = None
    active: Optional[bool] = None

class CustomerAssign(BaseModel):
    area:           Optional[str] = None
    salesperson_id: Optional[int] = None

class CustomerStatus(BaseModel):
    is_active: bool


# ── Salespersons ──────────────────────────────────────────────────────────────

@router.get("/salespersons")
def list_salespersons(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    sps = db.query(Salesperson).order_by(Salesperson.name).all()
    result = []
    for sp in sps:
        count = db.query(Customer).filter(Customer.salesperson_id == sp.id).count()
        result.append({
            "id": sp.id, "name": sp.name, "phone": sp.phone,
            "active": sp.active, "customer_count": count,
            "created_at": sp.created_at.isoformat() if sp.created_at else None,
        })
    return {"salespersons": result, "total": len(result)}


@router.post("/salespersons")
def create_salesperson(payload: SalespersonCreate, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    normalized = normalize_phone(payload.phone)
    if db.query(Salesperson).filter(Salesperson.phone == normalized).first():
        raise HTTPException(status_code=400, detail=f"Salesperson with phone {normalized} already exists")

    sp = Salesperson(name=payload.name.strip(), phone=normalized, active=True)
    db.add(sp); db.commit(); db.refresh(sp)

    try:
        send_whatsapp_message(
            sp.phone,
            f"👋 Hi {sp.name},\n\n"
            f"You've been added to the OrdeRR system for *{PLANT_NAME}*.\n\n"
            f"You will receive a daily WhatsApp message at *11:05 PM* "
            f"with a list of customers who haven't placed their order yet.\n\n"
            f"Please follow up with them for order collection.\n\n"
            f"— {PLANT_NAME} Team"
        )
    except Exception as e:
        print(f"⚠️ Welcome message failed (salesperson still created): {e}")

    return {"status": "created", "salesperson": {"id": sp.id, "name": sp.name, "phone": sp.phone, "active": sp.active}}


@router.put("/salespersons/{salesperson_id}")
def update_salesperson(salesperson_id: int, payload: SalespersonUpdate, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    sp = db.query(Salesperson).filter(Salesperson.id == salesperson_id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Salesperson not found")
    if payload.name   is not None: sp.name   = payload.name.strip()
    if payload.phone  is not None: sp.phone  = normalize_phone(payload.phone)
    if payload.active is not None: sp.active = payload.active
    db.commit(); db.refresh(sp)
    return {"status": "updated", "salesperson": {"id": sp.id, "name": sp.name, "phone": sp.phone, "active": sp.active}}


@router.delete("/salespersons/{salesperson_id}")
def deactivate_salesperson(salesperson_id: int, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    sp = db.query(Salesperson).filter(Salesperson.id == salesperson_id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Salesperson not found")
    sp.active = False; db.commit()
    affected = db.query(Customer).filter(Customer.salesperson_id == salesperson_id).count()
    return {"status": "deactivated", "salesperson_id": salesperson_id, "affected_customers": affected}


# ── Customers ─────────────────────────────────────────────────────────────────

def _customer_row(c: Customer, db: Session) -> dict:
    sp_name = None
    if c.salesperson_id:
        sp = db.query(Salesperson).filter(Salesperson.id == c.salesperson_id).first()
        sp_name = sp.name if sp else None
    return {
        "id": c.id, "restaurant_name": c.restaurant_name,
        "phone_number": c.phone_number, "area": c.area,
        "salesperson_id": c.salesperson_id, "salesperson_name": sp_name,
        "is_active": c.is_active,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/customers")
def list_customers(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    customers = (
        db.query(Customer)
        .filter(Customer.onboarding_status == "active")
        .order_by(Customer.restaurant_name)
        .all()
    )
    ordered_today = {
        row[0] for row in
        db.query(Order.customer_phone)
        .filter(Order.delivery_date == today_str, Order.is_cancelled == False)
        .distinct().all()
    }
    result = []
    for c in customers:
        row = _customer_row(c, db)
        row["ordered_today"] = c.phone_number in ordered_today
        result.append(row)
    return {"customers": result, "total": len(result)}


@router.get("/customers/unassigned")
def list_unassigned_customers(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    customers = (
        db.query(Customer)
        .filter(Customer.onboarding_status == "active", Customer.salesperson_id == None)
        .order_by(Customer.created_at.desc())
        .all()
    )
    return {"customers": [_customer_row(c, db) for c in customers], "total": len(customers)}


@router.get("/customers/{customer_id}/orders")
def get_customer_orders(customer_id: int, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    orders = (
        db.query(Order)
        .filter(Order.customer_phone == customer.phone_number)
        .order_by(Order.created_at.desc())
        .limit(60)
        .all()
    )

    result = []
    for o in orders:
        result.append({
            "id"            : o.id,
            "delivery_date" : o.delivery_date,
            "delivery_time" : o.delivery_time,
            "status"        : o.status,
            "is_cancelled"  : o.is_cancelled,
            "is_unclear"    : o.is_unclear,
            "unclear_reason": o.unclear_reason,
            "raw_message"   : o.raw_message,
            "items"         : json.loads(o.parsed_items) if o.parsed_items else [],
            "created_at"    : o.created_at.isoformat() if o.created_at else None,
        })

    return {
        "customer_id"    : customer_id,
        "restaurant_name": customer.restaurant_name,
        "phone_number"   : customer.phone_number,
        "total_orders"   : len(result),
        "orders"         : result,
    }


@router.post("/customers/{customer_id}/assign")
def assign_customer(customer_id: int, payload: CustomerAssign, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if payload.area is not None:
        customer.area = payload.area.strip()
    if payload.salesperson_id is not None:
        sp = db.query(Salesperson).filter(Salesperson.id == payload.salesperson_id).first()
        if not sp:
            raise HTTPException(status_code=404, detail=f"Salesperson id={payload.salesperson_id} not found")
        customer.salesperson_id = payload.salesperson_id
    db.commit(); db.refresh(customer)
    sp_name = None
    if customer.salesperson_id:
        sp = db.query(Salesperson).filter(Salesperson.id == customer.salesperson_id).first()
        sp_name = sp.name if sp else None
    return {"status": "assigned", "customer": {"id": customer.id, "restaurant_name": customer.restaurant_name, "area": customer.area, "salesperson_id": customer.salesperson_id, "salesperson_name": sp_name}}


@router.put("/customers/{customer_id}/status")
def update_customer_status(customer_id: int, payload: CustomerStatus, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    customer.is_active = payload.is_active
    db.commit(); db.refresh(customer)
    return {"status": "updated", "customer": {"id": customer.id, "restaurant_name": customer.restaurant_name, "phone_number": customer.phone_number, "is_active": customer.is_active}}


# ── Pending orders ────────────────────────────────────────────────────────────

@router.get("/pending")
def get_pending_now(delivery_date: Optional[str] = None, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    if delivery_date:
        try:
            target_date = date.fromisoformat(delivery_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        target_date = get_delivery_date_for_now()

    grouped = get_pending_customers(db, target_date)
    result  = []
    total   = 0

    for sp_id, customers in grouped.items():
        sp_name = "Unassigned"; sp_phone = None
        if sp_id is not None:
            sp = db.query(Salesperson).filter(Salesperson.id == sp_id).first()
            sp_name = sp.name if sp else f"Unknown (id={sp_id})"
            sp_phone = sp.phone if sp else None
        result.append({
            "salesperson_id": sp_id, "salesperson_name": sp_name,
            "salesperson_phone": sp_phone, "pending_count": len(customers),
            "customers": [{"id": c.id, "restaurant_name": c.restaurant_name, "phone_number": c.phone_number, "area": c.area} for c in customers],
        })
        total += len(customers)

    return {"delivery_date": target_date.isoformat(), "total_pending": total, "groups": result}


# ── WhatsApp Window Status ────────────────────────────────────────────────────

def _window_status(last_inbound: datetime | None, now: datetime) -> dict:
    """
    Calculate 24hr window status from last inbound message timestamp.
    Returns status, hours_remaining, minutes_remaining, last_seen_ist.
    """
    if last_inbound is None:
        return {
            "status": "CLOSED",
            "status_label": "Never messaged",
            "hours_remaining": 0,
            "minutes_remaining": 0,
            "last_seen_ist": None,
            "last_seen_display": "Never",
        }

    # Ensure timezone-aware
    if last_inbound.tzinfo is None:
        last_inbound = last_inbound.replace(tzinfo=timezone.utc)

    window_expires = last_inbound + timedelta(hours=24)
    remaining = window_expires - now

    if remaining.total_seconds() <= 0:
        return {
            "status": "CLOSED",
            "status_label": "Closed",
            "hours_remaining": 0,
            "minutes_remaining": 0,
            "last_seen_ist": last_inbound.astimezone(IST).isoformat(),
            "last_seen_display": last_inbound.astimezone(IST).strftime("%d %b %H:%M"),
        }

    total_minutes = int(remaining.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours >= 4:
        status = "OPEN"
    elif hours >= 1:
        status = "AT_RISK"
    else:
        status = "AT_RISK"

    return {
        "status": status,
        "status_label": f"{hours}h {minutes}m left",
        "hours_remaining": hours,
        "minutes_remaining": minutes,
        "last_seen_ist": last_inbound.astimezone(IST).isoformat(),
        "last_seen_display": last_inbound.astimezone(IST).strftime("%d %b %H:%M"),
    }


@router.get("/window-status")
def get_window_status(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    """
    Returns 24hr WhatsApp window status for all stakeholders:
    - Manager (from MANAGER_PHONE env var)
    - All active salespersons
    - All active customers

    Status values: OPEN (>4hrs), AT_RISK (0–4hrs), CLOSED (expired or never)
    """
    now = datetime.now(timezone.utc)

    # Collect all phones we need to check
    stakeholders = []

    # Manager
    if MANAGER_PHONE:
        stakeholders.append({
            "name": "Manager",
            "phone": MANAGER_PHONE,
            "role": "manager",
        })

    # Active salespersons
    salespersons = db.query(Salesperson).filter(Salesperson.active == True).all()
    for sp in salespersons:
        stakeholders.append({
            "name": sp.name,
            "phone": sp.phone,
            "role": "salesperson",
        })

    # Active customers
    customers = (
        db.query(Customer)
        .filter(
            Customer.is_active == True,
            Customer.onboarding_status == "active",
        )
        .order_by(Customer.restaurant_name)
        .all()
    )
    for c in customers:
        stakeholders.append({
            "name": c.restaurant_name or c.phone_number,
            "phone": c.phone_number,
            "role": "customer",
        })

    # Fetch last inbound message timestamp for each phone in one query
    all_phones = [s["phone"] for s in stakeholders]

    last_seen_map = {}
    if all_phones:
        rows = (
            db.query(
                InboundMessage.customer_phone,
                func.max(InboundMessage.received_at).label("last_seen"),
            )
            .filter(InboundMessage.customer_phone.in_(all_phones))
            .group_by(InboundMessage.customer_phone)
            .all()
        )
        last_seen_map = {row.customer_phone: row.last_seen for row in rows}

    # Build result
    result = []
    closed_count = 0
    at_risk_count = 0

    for s in stakeholders:
        last_inbound = last_seen_map.get(s["phone"])
        window = _window_status(last_inbound, now)
        result.append({
            "name"            : s["name"],
            "phone"           : s["phone"],
            "role"            : s["role"],
            **window,
        })
        if window["status"] == "CLOSED":
            closed_count += 1
        elif window["status"] == "AT_RISK":
            at_risk_count += 1

    return {
        "stakeholders"  : result,
        "total"         : len(result),
        "closed_count"  : closed_count,
        "at_risk_count" : at_risk_count,
        "checked_at"    : datetime.now(IST).strftime("%d %b %Y %H:%M IST"),
    }


# ── Test notifications ────────────────────────────────────────────────────────

from app.services.pending_notifier import (
    send_customer_reminders,
    notify_salespersons_pending,
    send_management_summary,
)
from app.services.reporter import send_daily_report

@router.post("/test-notifications/customer-reminders")
def test_customer_reminders(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    send_customer_reminders(db)
    return {"status": "sent"}

@router.post("/test-notifications/salesperson-pending")
def test_salesperson_pending(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    notify_salespersons_pending(db)
    return {"status": "sent"}

@router.post("/test-notifications/management-summary")
def test_management_summary(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    send_management_summary(db)
    return {"status": "sent"}

@router.post("/test-notifications/daily-report")
def test_daily_report(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    send_daily_report(db)
    return {"status": "sent"}